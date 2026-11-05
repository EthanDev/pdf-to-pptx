"""One-process job queue, atomic records, bounded conversion children."""
import json
import multiprocessing
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import convert_worker
import fetch
import settings

DATA = Path(os.environ.get('DATA_DIR', './data')) / 'jobs'
MAX_ACTIVE = settings.MAX_ACTIVE
MAX_DATA_MB = settings.MAX_DATA_MB
KEEP_S = 24 * 3600
POOL = ThreadPoolExecutor(max_workers=settings.WORKERS, thread_name_prefix='conv')
_lock = threading.RLock()
ALLOW_PRIVATE = False  # Tests only; no environment or caller setting.


class TooManyJobs(Exception):
    pass


class ServiceFull(Exception):
    pass


def _p(jid):
    return DATA / jid


def _write(jid, rec):
    directory = _p(jid)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.job-', delete=False, encoding='utf-8') as handle:
            temporary = Path(handle.name)
            json.dump(rec, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / 'job.json')
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def get(jid):
    if not re.fullmatch(r'[0-9a-f]{12}', jid or ''):
        return None
    try:
        return json.loads((_p(jid) / 'job.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _clean():
    with _lock:
        now = time.time()
        for directory in DATA.glob('*'):
            record = get(directory.name)
            if record and record.get('state') in ('done', 'failed'):
                try:
                    finished = record.get('finished_at', (directory / 'job.json').stat().st_mtime)
                    if now - finished > KEEP_S:
                        shutil.rmtree(directory)
                except OSError:
                    pass


def _disk_bytes():
    total = 0
    for path in DATA.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # A worker just removed its input or temporary record.
    return total


def recover():
    DATA.mkdir(parents=True, exist_ok=True)
    for directory in DATA.glob('*'):
        record = get(directory.name)
        if record and record['state'] in ('queued', 'running'):
            record.update(state='failed', error='Service restarted while running. Start it again.', finished_at=time.time())
            _write(directory.name, record)
        if directory.is_dir():
            for name in ('in.pdf', 'out.pptx', 'result.json'):
                (directory / name).unlink(missing_ok=True)
    _clean()


def start(url: str, *, limits=None):
    options = settings.LIMITS | (limits or {})
    DATA.mkdir(parents=True, exist_ok=True)
    with _lock:
        _clean()
        if _disk_bytes() > MAX_DATA_MB * 1_000_000:
            raise ServiceFull('The service is full. Try again later.')
        active = sum((get(d.name) or {}).get('state') in ('queued', 'running') for d in DATA.glob('*'))
        if active >= MAX_ACTIVE:
            raise TooManyJobs(f'Too many conversions in progress ({MAX_ACTIVE}). Try again in a few minutes.')
        jid = uuid.uuid4().hex[:12]
        _p(jid).mkdir()
        _write(jid, {'job_id': jid, 'state': 'queued', 'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        POOL.submit(_run, jid, url, options)
    return jid


def _run(jid, url, limits):
    record = get(jid)
    if record is None:
        return
    record['state'] = 'running'
    _write(jid, record)
    directory = _p(jid)
    child = None
    try:
        pdf, name = fetch.fetch_pdf(url, max_bytes=int(limits['max_pdf_mb'] * 1_000_000), allow_private=ALLOW_PRIVATE)
        (directory / 'in.pdf').write_bytes(pdf)
        del pdf
        child = multiprocessing.get_context('spawn').Process(target=convert_worker.run, args=(str(directory.resolve()), limits))
        child.start()
        child.join(limits['convert_timeout_s'])
        if child.is_alive():
            child.kill()
            child.join()
            raise ValueError(f"The conversion took longer than {limits['convert_timeout_s']:g} seconds and was stopped.")
        result_path = directory / 'result.json'
        if child.exitcode != 0 or not result_path.exists():
            raise ValueError('The conversion stopped unexpectedly (it may have run out of memory or time).')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if not result['ok']:
            raise ValueError(result['error'])
        output = (re.sub(r'[^A-Za-z0-9._-]', '_', name.rsplit('.', 1)[0])[:80] or 'document') + '.pptx'
        if output == 'out.pptx':
            output = 'out-converted.pptx'
        size = (directory / 'out.pptx').stat().st_size
        if size > limits['max_pptx_mb'] * 1_000_000:
            raise ValueError(f"The converted deck is bigger than {limits['max_pptx_mb']:g} MB.")
        (directory / 'out.pptx').replace(directory / output)
        record.update(state='done', file=output, size_bytes=size, report=result['report'])
    except (fetch.FetchError, ValueError) as exc:
        record.update(state='failed', error=str(exc))
    except Exception:
        record.update(state='failed', error='The conversion stopped unexpectedly (it may have run out of memory or time).')
    finally:
        if child and child.is_alive():
            child.kill()
            child.join()
        if child:
            child.close()
        for name in ('in.pdf', 'out.pptx', 'result.json'):
            (directory / name).unlink(missing_ok=True)
        record['finished_at'] = time.time()
        _write(jid, record)


def read_file(record):
    return (DATA / record['job_id'] / record['file']).read_bytes()


CLOUD = os.environ.get('JOB_BACKEND') == 'gcp'
backend = None
if CLOUD:
    import cloud_google
    from cloud_state import Full, TooMany
    backend = cloud_google.build()
    start, get = backend.start, backend.get
    _clean, recover = backend.clean_throttled, backend.recover
    read_file = backend.read_file
    ServiceFull, TooManyJobs = Full, TooMany
