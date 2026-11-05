"""Private Cloud Tasks worker. The runtime identity has no project roles."""
import asyncio
import ctypes
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import httpx

import convert_worker
import fetch

for name in ('httpx', 'httpx2', 'httpcore', 'google'):
    logging.getLogger(name).setLevel(logging.WARNING)
app = FastAPI()


class Delivery(BaseModel):
    job_id: str = Field(pattern=r'^[0-9a-f]{12}$')
    credential: str = Field(pattern=r'^[0-9a-f]{64}$')


def parent_death(expected_parent=None):
    if sys.platform == 'linux':
        parent = expected_parent if expected_parent is not None else os.getppid()
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0:
            raise RuntimeError('Parent-death protection is unavailable.')
        if os.getppid() != parent:
            os.kill(os.getpid(), signal.SIGKILL)


def attempt(jid, credential, coordinator, directory, expected_parent):
    os.setsid()
    parent_death(expected_parent)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(300)
    # Do not send native PDF warnings or library exceptions to service logs.
    with open(os.devnull, 'w') as null:
        os.dup2(null.fileno(), 1)
        os.dup2(null.fileno(), 2)
    base = coordinator.rstrip('/')+'/internal/jobs/'+jid
    aid = ''
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        def post(suffix, body, secret):
            response = client.post(base+'/'+suffix, json=body, headers={'Authorization': 'Bearer '+secret})
            if response.status_code == 409:
                raise RuntimeError('Active lease')
            response.raise_for_status()
            return response.json()
        try:
            claim = post('claim', {}, credential)
            if claim['terminal']:
                return
            aid, credential = claim['attempt_id'], claim['credential']
            signal.alarm(max(1, int(claim.get('budget', 300))))
            limits = claim['limits'] | {'parent_death': True, 'expected_parent': os.getpid()}
            root = Path(directory)
            data, name = fetch.fetch_pdf(claim['url'], max_bytes=int(limits['max_pdf_mb']*1_000_000))
            (root/'in.pdf').write_bytes(data)
            del data
            process = multiprocessing.get_context('spawn').Process(target=convert_worker.run, args=(directory, limits))
            process.start()
            try:
                process.join(limits['convert_timeout_s'])
                if process.is_alive():
                    process.kill(); process.join()
                    raise ValueError(f"The conversion took longer than {limits['convert_timeout_s']:g} seconds and was stopped.")
                if process.exitcode or not (root/'result.json').exists():
                    raise ValueError('The conversion stopped unexpectedly (it may have run out of memory or time).')
                result = json.loads((root/'result.json').read_text())
                if not result['ok']:
                    raise ValueError(result['error'])
            finally:
                if process.is_alive():
                    process.kill(); process.join()
                process.close()
            deck = root/'out.pptx'
            size = deck.stat().st_size
            uri = post('upload', {'attempt_id': aid, 'size': size}, credential)['upload_url']
            parsed = urlsplit(uri)
            if parsed.scheme != 'https' or parsed.hostname != 'storage.googleapis.com':
                raise RuntimeError('Invalid upload endpoint')
            with deck.open('rb') as handle:
                response = client.put(uri, content=handle, timeout=90,
                                      headers={'Content-Length': str(size), 'Content-Range': f'bytes 0-{size-1}/{size}'})
            response.raise_for_status()
            filename = (re.sub(r'[^A-Za-z0-9._-]', '_', name.rsplit('.',1)[0])[:80] or 'document')+'.pptx'
            post('finish', {'attempt_id': aid, 'filename': filename, 'report': result['report']}, credential)
        except (fetch.FetchError, ValueError) as error:
            if not aid:
                raise RuntimeError('The attempt could not be claimed.') from None
            post('fail', {'attempt_id': aid, 'error': str(error)[:400], 'retry': False}, credential)
        except Exception:
            if aid:
                try:
                    post('fail', {'attempt_id': aid, 'error': 'A temporary service error interrupted the conversion.', 'retry': True}, credential)
                except Exception:
                    pass
            raise RuntimeError('Temporary worker failure') from None


@app.get('/health')
def health():
    return {'ok': True}


@app.post('/execute')
async def execute(body: Delivery):
    coordinator = os.environ['COORDINATOR_URL']
    if urlsplit(coordinator).scheme != 'https' or not urlsplit(coordinator).hostname.endswith('.run.app'):
        return JSONResponse({'ok': False}, status_code=503)
    temporary = tempfile.TemporaryDirectory(prefix='pptx-')
    process = multiprocessing.get_context('spawn').Process(target=attempt,
        args=(body.job_id, body.credential, coordinator, temporary.name, os.getpid()))
    process.start()
    deadline = time.monotonic()+300
    try:
        while process.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(.1)
        if process.is_alive():
            return JSONResponse({'ok': False}, status_code=503)
        return JSONResponse({'ok': process.exitcode == 0}, status_code=200 if process.exitcode == 0 else 503)
    finally:
        if process.is_alive():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.kill()
        process.join()
        process.close()
        temporary.cleanup()
