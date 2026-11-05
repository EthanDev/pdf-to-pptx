"""Explicit live storage/MCP checks. Never prints credentials or signed URLs."""
import argparse
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import uuid

import httpx
from google.oauth2.credentials import Credentials
from pptx import Presentation
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gcp import PROJECT, ACCOUNT, EVIDENCE
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from cloud_google import Objects


def output(name, data):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE/name).write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data))


def private_value(args):
    result=subprocess.run(['gcloud',*args,'--project='+PROJECT,'--account='+ACCOUNT,'--quiet'],capture_output=True)
    if result.returncode:raise RuntimeError('Google authorization failed; refresh the project owner login.')
    return result.stdout.decode().strip()


def precondition_rejected(response):
    if response.status_code != 412:
        return False
    try:
        error = response.json()['error']
        return error.get('code') == 412 and any(item.get('reason') == 'conditionNotMet' for item in error.get('errors', []))
    except (ValueError, KeyError, TypeError):
        return False


def size_rejected(response):
    if response.status_code == 413:
        return True
    if response.status_code != 400:
        return False
    try:
        error = response.json()['error']
        message = error.get('message', '').lower()
        reasons = {item.get('reason') for item in error.get('errors', [])}
        return bool(reasons & {'invalid', 'invalidArgument', 'badRequest', 'contentLengthExceeded'}) and (
            'size' in message or 'content-length' in message or 'content length' in message) and any(
                word in message for word in ('match', 'expected', 'exceed', 'large', 'maximum', 'specified'))
    except (ValueError, KeyError, TypeError):
        return False


def storage_check():
    credentials=Credentials(token=private_value(['auth','print-access-token']))
    objects=Objects(PROJECT,PROJECT+'-results','pptx-reader@'+PROJECT+'.iam.gserviceaccount.com',credentials=credentials)
    prefix='attempts/live-proof-'+uuid.uuid4().hex
    size=512*1024;data=b'P'*(size//2)
    uri=objects.session(prefix+'/late.pptx',size)
    with httpx.Client(timeout=30,trust_env=False,follow_redirects=False) as client:
        first=client.put(uri,content=data,headers={'Content-Length':str(len(data)),
                         'Content-Range':f'bytes 0-{len(data)-1}/{size}'})
        if first.status_code!=308:raise RuntimeError('Partial upload did not remain pending.')
        # Leave the session active deliberately: a lost cancellation response
        # must not allow completion after the create-once object was fenced.
        objects.fence(prefix+'/late.pptx')
        late=client.put(uri,content=data,headers={'Content-Length':str(len(data)),
                        'Content-Range':f'bytes {len(data)}-{size-1}/{size}'})
        meta=objects.metadata(prefix+'/late.pptx')
        if not precondition_rejected(late) or not meta or meta['size']!=0:
            raise RuntimeError('Live late-upload fencing failed. Do not deploy.')
        objects.cancel(uri)
        key=prefix+'/once.pptx';one=objects.session(key,3);two=objects.session(key,3)
        a=client.put(one,content=b'PKx',headers={'Content-Length':'3','Content-Range':'bytes 0-2/3'})
        b=client.put(two,content=b'PKy',headers={'Content-Length':'3','Content-Range':'bytes 0-2/3'})
        if a.status_code not in (200,201) or not precondition_rejected(b):
            raise RuntimeError('Live create-once upload replay was accepted. Do not deploy.')
        objects.cancel(two);objects.fence(key)
        limited=prefix+'/limited.pptx';session=objects.session(limited,3)
        large=client.put(session,content=b'PKxx',headers={'Content-Length':'4','Content-Range':'bytes 0-3/4'})
        if not size_rejected(large) or objects.metadata(limited) is not None:
            raise RuntimeError('Live upload size bound failed. Do not deploy.')
        objects.cancel(session);objects.fence(limited)
    output('live-storage-proof.json',{'status':'pass','late_upload_http':late.status_code,
           'replayed_upload_http':b.status_code,'oversize_http':large.status_code,
           'proof_prefix':prefix,'tombstones':'zero-byte; retained by the 8-day lifecycle'})


async def mcp_check():
    state=json.loads((EVIDENCE/'cloud-resources.json').read_text())
    base=state['api_url']
    version=state['secret_versions']['pptx-api-token']
    token=private_value(['secrets','versions','access',version,'--secret=pptx-api-token'])
    async with httpx.AsyncClient(timeout=60,trust_env=False) as http:
        health=await http.get(base+'/health');assert health.status_code==200 and health.json()=={'ok':True}
        source=await http.get(base+'/source');assert source.json()['revision']==state['source_revision']
        absent=await http.post(base+'/mcp',json={})
        assert absent.status_code==401 and absent.headers.get('www-authenticate')=='Bearer'
        bad=await http.post(base+'/mcp',json={},headers={'Authorization':'Bearer invalid-check'})
        assert bad.status_code==401
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=60,trust_env=False) as http:
        async with streamable_http_client(base+'/mcp',http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                names=sorted(t.name for t in (await client.list_tools()).tools)
                assert names==['convert_pdf','get_pptx_file','job_status']
                async def convert(url):
                    result=await client.call_tool('convert_pdf',{'url':url,'wait_seconds':0})
                    assert not result.isError
                    job=result.structuredContent
                    for _ in range(120):
                        if job['state'] in ('done','failed'):return job
                        await asyncio.sleep(2)
                        result=await client.call_tool('job_status',{'job_id':job['job_id']})
                        assert not result.isError
                        job=result.structuredContent
                    raise RuntimeError('Live conversion did not reach a terminal state.')
                refused=await convert('https://127.0.0.1/refused.pdf')
                assert refused['state']=='failed' and 'private' in refused['error']
                # Existing public W3C fixture; this publishes no private/synthetic files.
                job=await convert('https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf')
                assert job['state']=='done'
                async with httpx.AsyncClient(timeout=60,trust_env=False) as download:
                    response=await download.get(job['pptx_url'])
                assert 'authorization' not in response.request.headers
                if response.status_code!=200:raise RuntimeError('Signed download failed.')
                raw=response.content
                assert raw.startswith(b'PK') and len(Presentation(io.BytesIO(raw)).slides)==job['report']['pages']
                result=await client.call_tool('get_pptx_file',{'job_id':job['job_id']})
                assert not result.isError
                inline=result.structuredContent
                assert base64.b64decode(inline['data_base64'])==raw
                assert hashlib.sha256(raw).hexdigest()==inline['sha256']
                (EVIDENCE/'live-converted.pptx').write_bytes(raw)
    output('live-mcp-proof.json',{'status':'pass','endpoint':base+'/mcp','source_revision':state['source_revision'],
        'tools':names,'missing_token':401,'wrong_token':401,'refused_job':refused['job_id'],
        'converted_job':job['job_id'],'pages':job['report']['pages'],'text_boxes':job['report']['text_boxes'],
        'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'paid_ai_jobs':0})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['storage','mcp'])
    try:
        action=parser.parse_args().action
        storage_check() if action=='storage' else asyncio.run(mcp_check())
    except Exception as error:
        # Transport exceptions can embed signed URLs; expose the type only.
        print('Live check failed: '+type(error).__name__+'; private details withheld.',file=sys.stderr)
        raise SystemExit(1)
