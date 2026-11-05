"""Cloud API auth boundaries and unchanged MCP/REST behavior; no network."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
os.environ.update(API_TOKEN='t'*32,DATA_DIR=tempfile.mkdtemp(),PUBLIC_URL='http://x',
                  SERVICE_URL='https://api.example.run.app',MAINTENANCE_IDENTITY='scheduler@example.iam.gserviceaccount.com')
import jobs,main,cloud_routes
from cloud_state import TooMany,Full
from test_cloud_state import Repo,Objects,Queue
from cloud_state import CloudJobs
import settings
from fastapi.testclient import TestClient

# main already mounted MCP at '/', so place internal routes before that mount.
main.app.router.routes[-1:-1]=cloud_routes.router.routes
AUTH={'Authorization':'Bearer '+'t'*32}


class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clock=1000
        cls.backend=CloudJobs(Repo(),Objects(),Queue(),b'k'*32,settings.LIMITS,now=lambda:cls.clock)
        cls.patches=[patch.object(jobs,'CLOUD',True),patch.object(jobs,'backend',cls.backend),
                     patch.object(jobs,'start',cls.backend.start),patch.object(jobs,'get',cls.backend.get),
                     patch.object(jobs,'read_file',cls.backend.read_file),patch.object(jobs,'recover',cls.backend.recover),
                     patch.object(jobs,'TooManyJobs',TooMany),patch.object(jobs,'ServiceFull',Full)]
        for p in cls.patches:p.start()
        cls.context=TestClient(main.app);cls.client=cls.context.__enter__()
    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None,None,None)
        for p in reversed(cls.patches):p.stop()
    def test_internal_scope(self):
        jid=self.backend.start('https://source.example/a.pdf')
        self.assertEqual(self.client.post(f'/internal/jobs/{jid}/claim',headers=AUTH).status_code,403)
        cap=self.backend.capability(jid)
        claim=self.client.post(f'/internal/jobs/{jid}/claim',headers={'Authorization':'Bearer '+cap})
        self.assertEqual(claim.status_code,200)
        result=claim.json()
        wrong=self.client.post(f'/internal/jobs/{jid}/upload',headers={'Authorization':'Bearer '+cap},
                               json={'attempt_id':result['attempt_id'],'size':10})
        self.assertEqual(wrong.status_code,403)
    def test_maintenance_requires_allowlisted_google_identity(self):
        with patch.object(cloud_routes,'verify_oauth2_token',side_effect=ValueError):
            self.assertEqual(self.client.post('/internal/maintenance',headers=AUTH).status_code,403)
        with patch.object(cloud_routes,'verify_oauth2_token',return_value={'email_verified':True,'email':'wrong@example.com'}):
            self.assertEqual(self.client.post('/internal/maintenance',headers=AUTH).status_code,403)
        with patch.object(cloud_routes,'verify_oauth2_token',return_value={'email_verified':True,'email':os.environ['MAINTENANCE_IDENTITY']}) as verify:
            self.assertEqual(self.client.post('/internal/maintenance',headers={'Authorization':'Bearer google-test'}).status_code,200)
            self.assertEqual(verify.call_args.kwargs['audience'],os.environ['SERVICE_URL'])
    def test_cloud_rest_mcp_and_source(self):
        self.assertEqual(self.client.get('/health').json(),{'ok':True})
        self.assertEqual(self.client.get('/source').json()['license'],'AGPL-3.0-only')
        self.assertEqual(self.client.post('/mcp',json={}).status_code,401)
        headers=AUTH|{'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
        result=self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).json()['result']
        self.assertEqual(sorted(t['name'] for t in result['tools']),['convert_pdf','get_pptx_file','job_status'])
        with patch.object(jobs,'start',side_effect=TooMany('queue full')):
            self.assertEqual(self.client.post('/convert',headers=AUTH,json={'url':'https://x/a.pdf'}).status_code,429)
        with patch.object(jobs,'start',side_effect=Full('The service is full. Try again later.')):
            self.assertEqual(self.client.post('/convert',headers=AUTH,json={'url':'https://x/a.pdf'}).status_code,503)
    def test_done_view_hides_credentials_and_bounds_expiry(self):
        jid=self.backend.start('https://source.example/a.pdf');c=self.backend.claim(jid,self.backend.capability(jid))
        uri=self.backend.upload(jid,c['attempt_id'],c['credential'],3);self.backend.objects.upload(uri,b'PKx')
        self.backend.finish(jid,c['attempt_id'],c['credential'],{'pages':1},'file.pptx')
        response=self.client.get('/jobs/'+jid,headers=AUTH)
        self.assertEqual(response.status_code,200);value=response.json()
        self.assertNotIn('url',value);self.assertNotIn('credential',response.text);self.assertIn('generation=',value['pptx_url'])
        self.assertEqual(value['links_expire_in'],'900 seconds')
        self.assertEqual(self.client.get(f'/files/{jid}/file.pptx',headers=AUTH).content,b'PKx')
        HTTP.clock=value['expires_at']
        self.assertEqual(self.client.get('/jobs/'+jid,headers=AUTH).status_code,404)
        self.assertEqual(self.client.get(f'/files/{jid}/file.pptx',headers=AUTH).status_code,404)
    def test_exception_does_not_echo_secret_url(self):
        with patch.object(jobs,'get',side_effect=RuntimeError('https://storage.invalid/?upload_id=private-test')):
            response=self.client.get('/jobs/'+'a'*12,headers=AUTH)
            self.assertEqual(response.status_code,503);self.assertNotIn('private-test',response.text)


if __name__=='__main__':unittest.main()
