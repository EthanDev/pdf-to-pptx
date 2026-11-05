"""Failure/race checks of the production protocol with atomic in-memory adapters."""
import copy
import hashlib
from pathlib import Path
import sys
import threading
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from cloud_state import CloudJobs, COUNTERS, Busy, Full, Rejected, TooMany, jobkey, attemptkey
import settings


class Repo:
    def __init__(self):
        self.data, self.lock = {}, threading.RLock()
    def get(self, key):
        with self.lock:
            return copy.deepcopy(self.data.get(key))
    def atomic(self, keys, operation):
        with self.lock:
            values={k:copy.deepcopy(self.data.get(k)) for k in keys}
            result=operation(values)
            for k,v in values.items():
                if v is None:self.data.pop(k,None)
                else:self.data[k]=v
            return result
    def query(self, collection, field, value):
        with self.lock:
            return [copy.deepcopy(v) for k,v in self.data.items() if k.startswith(collection+'/') and v.get(field)==value]
    def active(self):
        with self.lock:
            return [copy.deepcopy(v) for k,v in self.data.items() if k.startswith('jobs/') and v['state'] not in ('done','failed','expired')]
    def expired(self, now):
        with self.lock:
            return [copy.deepcopy(v) for k,v in self.data.items() if k.startswith('jobs/') and v.get('expires_at',float('inf'))<=now]


class Objects:
    def __init__(self):
        self.data,self.sessions,self.generation={}, {}, 0
        self.fail_fence=False
        self.before_copy=None
    def put(self,key,data):
        self.generation+=1
        self.data[key]={'data':data,'size':len(data),'generation':self.generation,'md5':hashlib.md5(data).hexdigest()}
    def metadata(self,key):
        obj=self.data.get(key)
        return None if obj is None else {k:obj[k] for k in ('size','generation','md5')}
    def session(self,key,size):
        uri='https://storage.googleapis.com/session/'+uuid.uuid4().hex
        self.sessions[uri]={'key':key,'size':size,'cancelled':False}
        return uri
    def upload(self,uri,data):
        s=self.sessions[uri]
        if s['cancelled'] or s['key'] in self.data or len(data)!=s['size']:raise Rejected('Upload rejected')
        self.put(s['key'],data)
    def cancel(self,uri):
        self.sessions[uri]['cancelled']=True
    def fence(self,key):
        if self.fail_fence:raise RuntimeError('Storage unavailable')
        self.put(key,b'')
    def promote(self,key,generation,final):
        if self.before_copy:
            callback,self.before_copy=self.before_copy,None
            callback()
        if final not in self.data:
            if self.data[key]['generation']!=generation:raise Rejected('Source changed')
            self.put(final,self.data[key]['data'])
        return self.metadata(final)
    def signed_get(self,key,generation,seconds,filename):
        assert self.data[key]['generation']==generation
        return f'https://storage.example/{key}?generation={generation}&expires={seconds}'
    def read(self,key,generation):
        if self.data[key]['generation']!=generation:raise Rejected('Wrong generation')
        return self.data[key]['data']
    def stream(self,key,generation):
        yield self.read(key,generation)


class Queue:
    def __init__(self):self.entries,self.fail={},False
    def enqueue(self,jid,cap):
        self.entries[jid]=cap
        if self.fail:raise RuntimeError('Ambiguous create timeout')


class Protocol(unittest.TestCase):
    def setUp(self):
        self.time=1000
        self.repo,self.objects,self.queue=Repo(),Objects(),Queue()
        self.jobs=CloudJobs(self.repo,self.objects,self.queue,b'k'*32,settings.LIMITS,now=lambda:self.time)
    def start(self):return self.jobs.start('https://example.com/source.pdf')
    def claim(self,jid):return self.jobs.claim(jid,self.jobs.capability(jid))
    def upload(self,jid,c,data=b'PKexample'):
        uri=self.jobs.upload(jid,c['attempt_id'],c['credential'],len(data));self.objects.upload(uri,data);return uri
    def done(self,jid,c):
        self.jobs.finish(jid,c['attempt_id'],c['credential'],{'pages':1,'text_boxes':1},'deck.pptx')
    def counters(self):return self.repo.get(COUNTERS)

    def test_restart_and_shared_state(self):
        jid=self.start();c=self.claim(jid);self.upload(jid,c);self.done(jid,c)
        other=CloudJobs(self.repo,self.objects,self.queue,b'k'*32,settings.LIMITS,now=lambda:self.time)
        other.recover();j=other.get(jid)
        self.assertEqual(j['state'],'done');self.assertEqual(other.read_file(j),b'PKexample')
    def test_ambiguous_dispatch_and_outbox(self):
        self.queue.fail=True;jid=self.start();self.assertTrue(self.jobs.get(jid)['outbox'])
        self.queue.fail=False;self.jobs.maintain()
        self.assertFalse(self.jobs.get(jid)['outbox']);self.assertEqual(len(self.queue.entries),1)
    def test_atomic_admission(self):
        self.jobs.max_active=2;accepted=[]
        def run():
            try:accepted.append(self.start())
            except TooMany:pass
        threads=[threading.Thread(target=run) for _ in range(12)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(accepted),2);self.assertEqual(self.counters()['active'],2)
    def test_duplicate_claim_and_two_running(self):
        ids=[self.start() for _ in range(3)]
        self.claim(ids[0]);self.claim(ids[1])
        with self.assertRaises(Busy):self.claim(ids[0])
        with self.assertRaises(Busy):self.claim(ids[2])
        self.assertEqual(len(self.jobs.get(ids[0])['attempts']),1)
    def test_fenced_retry_rejects_stale_completion(self):
        jid=self.start();old=self.claim(jid);self.upload(jid,old)
        self.time+=601;self.jobs.maintain();new=self.claim(jid)
        with self.assertRaises(Rejected):self.done(jid,old)
        self.upload(jid,new);self.done(jid,new)
        self.assertEqual(self.counters()['active'],0);self.assertEqual(self.counters()['running'],0)
    def test_three_claims_not_three_deliveries(self):
        jid=self.start();c=self.claim(jid)
        for _ in range(6):
            with self.assertRaises(Busy):self.claim(jid)
        self.assertEqual(len(self.jobs.get(jid)['attempts']),1)
        for attempt in range(3):
            if attempt:c=self.claim(jid)
            self.jobs.fail(jid,c['attempt_id'],c['credential'],'temporary',retry=True)
        self.assertEqual(self.jobs.get(jid)['state'],'failed');self.assertEqual(self.counters()['active'],0)
    def test_completed_redelivery_and_idempotence(self):
        jid=self.start();c=self.claim(jid);self.upload(jid,c);self.done(jid,c);counts=self.counters()
        self.done(jid,c);self.jobs.maintain()
        self.assertTrue(self.claim(jid)['terminal']);self.assertEqual(self.counters(),counts)
    def test_upload_exact_size_and_create_once(self):
        jid=self.start();c=self.claim(jid);uri=self.upload(jid,c)
        with self.assertRaises(Rejected):self.objects.upload(uri,b'PKexample')
        with self.assertRaises(Rejected):self.jobs.upload(jid,c['attempt_id'],c['credential'],300_000_001)
        with self.assertRaises(Rejected):self.jobs.upload(jid,c['attempt_id'],c['credential'],4)
    def test_upload_other_job_and_callback_scope(self):
        one,two=self.start(),self.start();c=self.claim(one)
        with self.assertRaises(Rejected):self.jobs.upload(two,c['attempt_id'],c['credential'],10)
        with self.assertRaises(Rejected):self.jobs.claim(one,c['credential'])
        with self.assertRaises(Rejected):self.jobs.upload(one,c['attempt_id'],self.jobs.capability(one),10)
    def test_partial_or_missing_upload_never_done(self):
        jid=self.start();c=self.claim(jid);self.jobs.upload(jid,c['attempt_id'],c['credential'],10)
        with self.assertRaises(Rejected):self.done(jid,c)
        self.assertNotEqual(self.jobs.get(jid)['state'],'done')
    def test_storage_reservations_and_uncertainty(self):
        jid=self.start();c=self.claim(jid);uri=self.upload(jid,c)
        self.objects.fail_fence=True
        with self.assertRaises(RuntimeError):self.jobs.fail(jid,c['attempt_id'],c['credential'],'failed')
        self.assertEqual(self.counters()['reserved'],600_000_000)
        self.jobs.max_data=600_000_000
        with self.assertRaises(Full):self.start()
        self.objects.fail_fence=False;self.jobs.maintain();self.assertEqual(self.counters()['reserved'],0)
        with self.assertRaises(Rejected):self.objects.upload(uri,b'PKexample')
    def test_late_upload_cannot_resurrect_after_fence(self):
        jid=self.start();c=self.claim(jid)
        uri=self.jobs.upload(jid,c['attempt_id'],c['credential'],9)
        # Simulate an uncertain cancellation: the create-once object fence must
        # independently prevent a late commit by an already-issued session.
        self.objects.cancel=lambda _:None
        self.time+=601;self.jobs.maintain()
        with self.assertRaises(Rejected):self.objects.upload(uri,b'PKexample')
        self.assertEqual(self.counters()['reserved'],0)
    def test_copy_races_cleanup(self):
        jid=self.start();c=self.claim(jid);self.upload(jid,c)
        self.objects.before_copy=lambda:self.jobs.fail(jid,c['attempt_id'],c['credential'],'failed')
        with self.assertRaises(Rejected):self.done(jid,c)
        self.assertEqual(self.jobs.get(jid)['state'],'failed')
        self.assertTrue(all(o['size']==0 for o in self.objects.data.values()))
    def test_deadline_even_without_delivery(self):
        self.queue.fail=True;jid=self.start();self.time+=1801;self.jobs.maintain()
        self.assertEqual(self.jobs.get(jid)['state'],'failed');self.assertEqual(self.counters()['active'],0)
    def test_expiry_blocks_bearer_inline_and_fresh_links(self):
        jid=self.start();c=self.claim(jid);self.upload(jid,c);self.done(jid,c)
        j=self.jobs.get(jid);self.time=j['expires_at']-15
        link,remaining=self.jobs.download_url(j)
        self.assertEqual(remaining,15);self.assertIn('generation=',link)
        self.time+=15
        self.assertIsNone(self.jobs.get(jid))
        with self.assertRaises(Rejected):self.jobs.read_file(j)
        with self.assertRaises(Rejected):self.jobs.download_url(j)
        self.jobs.maintain();self.jobs.maintain()
        self.assertEqual(self.counters(),{'active':0,'running':0,'used':0,'reserved':0})
        self.assertIsNone(self.repo.get(jobkey(jid)))
    def test_failed_upload_cleanup_does_not_touch_other_winner(self):
        one,two=self.start(),self.start();c1,c2=self.claim(one),self.claim(two)
        self.upload(one,c1);self.done(one,c1)
        self.upload(two,c2);self.jobs.fail(two,c2['attempt_id'],c2['credential'],'failed')
        self.assertEqual(self.jobs.read_file(self.jobs.get(one)),b'PKexample')
    def test_slow_signing_cannot_extend_retention(self):
        jid=self.start();c=self.claim(jid);self.upload(jid,c);self.done(jid,c)
        j=self.jobs.get(jid);self.time=j['expires_at']-5
        deadlines=[]
        def sign(key,generation,deadline,filename):
            deadlines.append(deadline);self.time+=10;return 'https://storage.example/file'
        self.objects.signed_get=sign
        with self.assertRaises(Rejected):self.jobs.download_url(j)
        self.assertEqual(deadlines,[j['expires_at']])

    def test_capacity_fails_before_upload_grant(self):
        jid=self.start();self.jobs.max_data=599_999_999
        self.assertTrue(self.claim(jid)['terminal']);self.assertEqual(self.counters()['reserved'],0)


if __name__=='__main__':unittest.main()
