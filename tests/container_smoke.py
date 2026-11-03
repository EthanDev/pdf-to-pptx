"""Local Docker acceptance, with ephemeral credentials kept out of output."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def run(context):
    docker=['docker']+(['--context',context] if context else [])
    name='pdf-to-pptx-check-'+secrets.token_hex(4)
    token=secrets.token_hex(32)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    def command(*args):
        return subprocess.check_output(docker+list(args),stderr=subprocess.STDOUT,text=True)
    # A unique named volume avoids changing or deleting any existing volume.
    volume=name+'-data'
    with tempfile.NamedTemporaryFile(mode='w') as env:
        os.chmod(env.name,0o600)
        env.write(f'API_TOKEN={token}\nPUBLIC_URL={base}\n');env.flush()
        try:
            command('run','-d','--name',name,'--env-file',env.name,'-p',f'127.0.0.1:{port}:8080','-v',f'{volume}:/data','pdf-to-pptx')
            for _ in range(60):
                info=json.loads(command('inspect',name))[0]
                if info['State'].get('Health',{}).get('Status')=='healthy':break
                time.sleep(1)
            assert info['State']['Health']['Status']=='healthy'
            assert info['Config']['User']=='10001:10001'
            assert command('exec',name,'python','-c','import os; assert os.stat("/data").st_uid == 10001').strip()==''
            def request(path, payload=None, auth=False):
                headers={'Authorization':'Bearer '+token} if auth else {}
                if payload is not None:headers |= {'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
                req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
                try:
                    with urllib.request.urlopen(req,timeout=10) as response:return response.status,response.headers,json.load(response)
                except urllib.error.HTTPError as error:return error.code,error.headers,json.load(error)
            assert request('/health')[2]=={'ok':True}
            status,headers,_=request('/mcp',{})
            assert status==401 and headers['WWW-Authenticate']=='Bearer'
            def rpc(method,params=None):
                status,_,body=request('/mcp',{'jsonrpc':'2.0','id':1,'method':method,'params':params or {}},True)
                assert status==200
                return body['result']
            assert sorted(t['name'] for t in rpc('tools/list')['tools'])==['convert_pdf','get_pptx_file','job_status']
            assert rpc('tools/call',{'name':'job_status','arguments':{'job_id':'0'*12}})['isError']
            assert request('/convert',{'url':'https://127.0.0.1/a.pdf'})[0]==401
            assert request('/convert',{'url':'https://127.0.0.1/a.pdf'},True)[0]==202
            assert request('/jobs/'+'0'*12,auth=True)[0]==404
            logs=command('logs',name)
            assert token not in logs and 'sig=' not in logs
            print(json.dumps({'health':'healthy','user':'10001:10001','named_volume_owner':10001,'rows':[1,2,3,12,'15 REST 202/401',16,20],'logs_contain_secrets':False},indent=2))
        finally:
            subprocess.run(docker+['rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(docker+['volume','rm',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--context');run(parser.parse_args().context)
