"""Durable job protocol. Transactions contain no network/file side effects."""
import hashlib
import hmac
import re
import threading
import time
import uuid

TERMINAL = {'done', 'failed', 'expired'}
COUNTERS = 'meta/counters'


class Full(Exception):
    pass


class TooMany(Exception):
    pass


class Busy(Exception):
    pass


class Rejected(Exception):
    pass


def jid_ok(value):
    return bool(re.fullmatch(r'[0-9a-f]{12}', value or ''))


def aid_ok(value):
    return bool(re.fullmatch(r'[0-9a-f]{32}', value or ''))


def jobkey(jid):
    if not jid_ok(jid):
        raise Rejected('No such job.')
    return 'jobs/' + jid


def attemptkey(jid, aid):
    if not jid_ok(jid) or not aid_ok(aid):
        raise Rejected('No such attempt.')
    return f'attempts/{jid}-{aid}'


class CloudJobs:
    def __init__(self, repo, objects, queue, key, limits, *, now=time.time,
                 max_active=20, max_data_mb=2000):
        self.repo, self.objects, self.queue = repo, objects, queue
        self.key, self.limits, self.now = key, limits, now
        self.max_active, self.max_data = max_active, int(max_data_mb * 1_000_000)
        self._maintenance_lock = threading.Lock()
        self._next_clean = 0

    def capability(self, jid, aid=''):
        message = f'attempt:{jid}:{aid}' if aid else f'claim:{jid}'
        return hmac.new(self.key, message.encode(), hashlib.sha256).hexdigest()

    def authenticate(self, jid, token, aid=''):
        if not jid_ok(jid) or (aid and not aid_ok(aid)):
            raise Rejected('Invalid job credential.')
        expected = self.capability(jid, aid)
        if not token or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Rejected('Invalid job credential.')

    def start(self, url, *, limits=None):
        jid, now = uuid.uuid4().hex[:12], self.now()
        options = self.limits | (limits or {})
        def admit(d):
            c = d[COUNTERS] or {'active': 0, 'running': 0, 'used': 0, 'reserved': 0}
            if d[jobkey(jid)]:
                raise Busy('Please try again.')
            if c['active'] >= self.max_active:
                raise TooMany(f'Too many conversions in progress ({self.max_active}). Try again in a few minutes.')
            if c['used'] + c['reserved'] >= self.max_data:
                raise Full('The service is full. Try again later.')
            c['active'] += 1
            d[COUNTERS] = c
            d[jobkey(jid)] = {'job_id': jid, 'state': 'queued', 'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                             'created_at': now, 'deadline': now + 1800, 'outbox': True, 'url': url,
                             'attempts': [], 'attempt_id': '', 'limits': options, 'cleanup_pending': False}
            return jid
        self.repo.atomic([jobkey(jid), COUNTERS], admit)
        self.dispatch(jid)
        return jid

    def dispatch(self, jid):
        record = self.repo.get(jobkey(jid))
        if not record or not record.get('outbox') or record['state'] in TERMINAL:
            return
        try:
            self.queue.enqueue(jid, self.capability(jid))
        except Exception:
            # Durable outbox remains pending; scheduled maintenance retries it.
            return
        def sent(d):
            if d[jobkey(jid)]:
                d[jobkey(jid)]['outbox'] = False
        self.repo.atomic([jobkey(jid)], sent)

    def get(self, jid):
        if not jid_ok(jid):
            return None
        j = self.repo.get(jobkey(jid))
        if not j or j['state'] == 'expired' or j.get('expires_at', float('inf')) <= self.now():
            return None
        result = dict(j)
        if result['state'] == 'publishing':
            result['state'] = 'running'
        return result

    def _end(self, job, counters, state, error=''):
        if job['state'] not in TERMINAL:
            counters['active'] -= 1
        job.update(state=state, outbox=False, expires_at=self.now()+86400,
                   cleanup_pending=bool(job['attempts']))
        job.pop('url', None)
        if error:
            job['error'] = error

    def claim(self, jid, token):
        self.authenticate(jid, token)
        aid, now = uuid.uuid4().hex, self.now()
        jk, ak = jobkey(jid), attemptkey(jid, aid)
        def claim(d):
            j, c = d[jk], d[COUNTERS]
            if not j:
                raise Rejected('No such job.')
            if j['state'] in TERMINAL:
                return {'terminal': True}
            if now >= j['deadline']:
                self._end(j, c, 'failed', 'The job could not finish within 30 minutes. Start it again.')
                return {'terminal': True}
            if j['state'] != 'queued' or c['running'] >= 2:
                raise Busy('An attempt is already running. Try again later.')
            if len(j['attempts']) >= 3:
                self._end(j, c, 'failed', 'The conversion stopped after three attempts. Start it again.')
                return {'terminal': True}
            reserve = 2 * int(j['limits']['max_pptx_mb'] * 1_000_000)
            if c['used'] + c['reserved'] + reserve > self.max_data:
                self._end(j, c, 'failed', 'The service is full. Try again later.')
                return {'terminal': True}
            c['reserved'] += reserve
            c['running'] += 1
            j.update(state='running', attempt_id=aid, cleanup_pending=True)
            j['attempts'].append(aid)
            d[ak] = {'job_id': jid, 'attempt_id': aid, 'phase': 'active', 'lease': now+600,
                     'reservation': reserve, 'released': False, 'running_released': False,
                     'stage': f'attempts/{jid}/{aid}.pptx', 'final': f'final/{jid}/{aid}.pptx',
                     'upload_may_exist': False, 'copy_may_exist': False,
                     'stage_fenced': False, 'final_fenced': False, 'session': '', 'expected_size': 0}
            return {'terminal': False, 'attempt_id': aid, 'credential': self.capability(jid, aid),
                    'url': j['url'], 'limits': j['limits'], 'budget': max(1, min(300, int(j['deadline']-now-10)))}
        return self.repo.atomic([jk, ak, COUNTERS], claim)

    def _valid(self, j, a, *, phases=('active',)):
        if not j or not a or j['state'] in TERMINAL or j['attempt_id'] != a['attempt_id'] or a['phase'] not in phases:
            raise Rejected('This attempt is no longer active.')
        if self.now() >= min(j['deadline'], a['lease']):
            raise Rejected('This attempt has expired.')

    def upload(self, jid, aid, token, size):
        self.authenticate(jid, token, aid)
        jk, ak = jobkey(jid), attemptkey(jid, aid)
        def prepare(d):
            j, a = d[jk], d[ak]
            self._valid(j, a)
            if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= int(j['limits']['max_pptx_mb']*1_000_000):
                raise Rejected('The converted deck is too large or empty.')
            if a['expected_size'] not in (0, size):
                raise Rejected('The upload size changed.')
            a.update(upload_may_exist=True, expected_size=size)
            return dict(a)
        a = self.repo.atomic([jk, ak], prepare)
        if a['session']:
            return a['session']
        # All sessions, even ones lost in a crash here, target this create-once key.
        session = self.objects.session(a['stage'], size)
        def remember(d):
            self._valid(d[jk], d[ak])
            d[ak]['session'] = session
        try:
            self.repo.atomic([jk, ak], remember)
        except Exception:
            self.objects.cancel(session)
            self.objects.fence(a['stage'])
            raise
        return session

    def finish(self, jid, aid, token, report, filename):
        self.authenticate(jid, token, aid)
        jk, ak = jobkey(jid), attemptkey(jid, aid)
        current = self.repo.get(jk)
        if current and current['state'] == 'done' and current['attempt_id'] == aid:
            return
        def publishing(d):
            j, a = d[jk], d[ak]
            self._valid(j, a, phases=('active', 'publishing'))
            if not a['upload_may_exist']:
                raise Rejected('There is no uploaded result.')
            a.update(phase='publishing', copy_may_exist=True)
            j['state'] = 'publishing'
            return dict(a)
        a = self.repo.atomic([jk, ak], publishing)
        meta = self.objects.metadata(a['stage'])
        if not meta or meta['size'] != a['expected_size'] or not meta['size']:
            raise Rejected('The uploaded result is incomplete.')
        final = self.objects.promote(a['stage'], meta['generation'], a['final'])
        if final['size'] != meta['size'] or final['md5'] != meta['md5']:
            raise Rejected('The uploaded result could not be verified.')
        if not isinstance(report, dict) or not isinstance(report.get('pages'), int):
            raise Rejected('The result report is invalid.')
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename)[:100]
        if not safe_name.endswith('.pptx'):
            safe_name = 'document.pptx'
        def publish(d):
            j, attempt, c = d[jk], d[ak], d[COUNTERS]
            self._valid(j, attempt, phases=('publishing',))
            self._end(j, c, 'done')
            j.update(file=safe_name, size_bytes=final['size'], report=report,
                     object=attempt['final'], generation=final['generation'], size_accounted=True)
            c['used'] += final['size']
            attempt['phase'] = 'published'
        self.repo.atomic([jk, ak, COUNTERS], publish)
        self.cleanup(jid, aid)

    def fail(self, jid, aid, token, error, *, retry=False):
        self.authenticate(jid, token, aid)
        jk, ak = jobkey(jid), attemptkey(jid, aid)
        def failed(d):
            j, a, c = d[jk], d[ak], d[COUNTERS]
            if not j or not a or j['state'] in TERMINAL or j['attempt_id'] != aid or a['phase'] not in ('active', 'publishing'):
                return
            a['phase'] = 'fencing'
            if retry and self.now() < j['deadline'] and len(j['attempts']) < 3:
                j['state'] = 'retrying'
            else:
                self._end(j, c, 'failed', error)
        self.repo.atomic([jk, ak, COUNTERS], failed)
        self.cleanup(jid, aid)

    def cleanup(self, jid, aid):
        jk, ak = jobkey(jid), attemptkey(jid, aid)
        def fence_intent(d):
            j, a = d[jk], d[ak]
            if not j or not a:
                return None
            winner = j['state'] == 'done' and j['attempt_id'] == aid
            if winner and a['phase'] == 'published' and a['stage_fenced'] and a['released']:
                return None
            if not winner and a['phase'] == 'fenced' and a['final_fenced'] and a['released'] and not (j['state'] == 'expired' and j.get('size_accounted')):
                return None
            if a['phase'] in ('active', 'publishing') and self.now() < a['lease'] and j['state'] not in TERMINAL:
                return None
            a['phase'] = 'cleaning_published' if winner else 'fencing'
            return dict(a), winner
        selected = self.repo.atomic([jk, ak], fence_intent)
        if not selected:
            return
        a, winner = selected
        if a['session']:
            self.objects.cancel(a['session'])
        if a['upload_may_exist'] and not a['stage_fenced']:
            self.objects.fence(a['stage'])
        if not winner and a['copy_may_exist'] and not a['final_fenced']:
            self.objects.fence(a['final'])
        def release(d):
            j, attempt, c = d[jk], d[ak], d[COUNTERS]
            if not j or not attempt:
                return
            attempt['stage_fenced'] = True
            attempt['session'] = ''
            if not winner:
                attempt['final_fenced'] = True
            if not attempt['released']:
                c['reserved'] -= attempt['reservation']
                attempt['released'] = True
            if not attempt['running_released']:
                c['running'] -= 1
                attempt['running_released'] = True
            if j['state'] == 'expired' and j.get('size_accounted') and attempt['final_fenced'] and j['attempt_id'] == aid:
                c['used'] -= j['size_bytes']
                j['size_accounted'] = False
            attempt['phase'] = 'published' if winner else 'fenced'
            if j['state'] in ('running', 'publishing', 'retrying') and j['attempt_id'] == aid:
                if self.now() < j['deadline'] and len(j['attempts']) < 3:
                    j['state'] = 'queued'
                else:
                    self._end(j, c, 'failed', 'The conversion could not finish. Start it again.')
        self.repo.atomic([jk, ak, COUNTERS], release)
        self.clear_cleanup_flag(jid)

    def clear_cleanup_flag(self, jid):
        snapshot = self.repo.get(jobkey(jid))
        if not snapshot:
            return
        aids = list(snapshot['attempts'])
        keys = [jobkey(jid)] + [attemptkey(jid, aid) for aid in aids]
        def clear(d):
            j = d[jobkey(jid)]
            if not j or j['attempts'] != aids:
                return
            for aid in aids:
                a = d[attemptkey(jid, aid)]
                if not a or not a['released'] or not a['stage_fenced']:
                    return
                if not a['final_fenced'] and not (j['state'] == 'done' and j['attempt_id'] == aid):
                    return
            j['cleanup_pending'] = False
        self.repo.atomic(keys, clear)

    def maintain(self):
        # Cloud Tasks retries uncertain dispatches with the same deterministic ID.
        for j in self.repo.query('jobs', 'outbox', True):
            self.dispatch(j['job_id'])
        for j in self.repo.query('jobs', 'cleanup_pending', True):
            jid = j['job_id']
            for aid in j['attempts']:
                a = self.repo.get(attemptkey(jid, aid))
                if a and (a['phase'] not in ('active', 'publishing') or a['lease'] <= self.now() or j['state'] in TERMINAL):
                    self.cleanup(jid, aid)
            self.clear_cleanup_flag(jid)
        for j in self.repo.active():
            if j['deadline'] <= self.now():
                def end(d):
                    if d[jobkey(j['job_id'])] and d[jobkey(j['job_id'])]['state'] not in TERMINAL:
                        self._end(d[jobkey(j['job_id'])], d[COUNTERS], 'failed', 'The job could not finish within 30 minutes. Start it again.')
                self.repo.atomic([jobkey(j['job_id']), COUNTERS], end)
        for j in self.repo.expired(self.now()):
            jid = j['job_id']
            def expire(d):
                if d[jobkey(jid)] and d[jobkey(jid)].get('expires_at', float('inf')) <= self.now():
                    d[jobkey(jid)]['state'] = 'expired'
            self.repo.atomic([jobkey(jid)], expire)
            for aid in j['attempts']:
                self.cleanup(jid, aid)
            keys = [jobkey(jid), COUNTERS] + [attemptkey(jid, aid) for aid in j['attempts']]
            def remove(d):
                current = d[jobkey(jid)]
                if not current or current['state'] != 'expired' or current.get('size_accounted'):
                    return
                if any(d[k] and (not d[k]['released'] or not d[k]['final_fenced']) for k in keys[2:]):
                    return
                for k in keys:
                    if k != COUNTERS:
                        d[k] = None
            self.repo.atomic(keys, remove)

    def clean_throttled(self):
        # Requests do not perform remote I/O before authentication or on /health.
        if self.now() < self._next_clean or not self._maintenance_lock.acquire(False):
            return
        try:
            self.maintain()
            self._next_clean = self.now()+30
        finally:
            self._maintenance_lock.release()

    def recover(self):
        # Starting another instance must not fail another instance's work.
        return None

    def download_url(self, j):
        deadline = min(int(self.now()) + 900, int(j['expires_at']))
        if deadline <= self.now():
            raise Rejected('This file has expired.')
        url = self.objects.signed_get(j['object'], j['generation'], deadline, j['file'])
        remaining = int(deadline - self.now())
        if remaining <= 0:
            raise Rejected('This file has expired.')
        return url, remaining

    def read_file(self, j):
        if j['expires_at'] <= self.now():
            raise Rejected('This file has expired.')
        return self.objects.read(j['object'], j['generation'])

    def stream_file(self, j):
        if j['expires_at'] <= self.now():
            raise Rejected('This file has expired.')
        return self.objects.stream(j['object'], j['generation'])
