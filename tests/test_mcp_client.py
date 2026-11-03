"""A real MCP SDK client over loopback HTTP; no external PDF request."""
import asyncio
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT=Path(__file__).resolve().parents[1]


async def check(base, token):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=15) as http:
        async with streamable_http_client(base+'/mcp',http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                assert sorted(t.name for t in (await client.list_tools()).tools)==['convert_pdf','get_pptx_file','job_status']
                result=await client.call_tool('convert_pdf',{'url':'https://127.0.0.1/not-allowed.pdf','wait_seconds':5})
                assert not result.isError
                job=result.structuredContent
                assert job['state']=='failed' and 'private' in job['error'], job
                status=await client.call_tool('job_status',{'job_id':job['job_id']})
                assert not status.isError and status.structuredContent['state']=='failed'


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}';token=secrets.token_hex(32)
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile(mode='w+') as logs:
        env=os.environ | {'API_TOKEN':token,'LINK_KEY':secrets.token_hex(32),'PUBLIC_URL':base,
                          'DATA_DIR':directory,'PYTHONPATH':str(ROOT/'app')}
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--app-dir',str(ROOT/'app'),
                               '--host','127.0.0.1','--port',str(port),'--workers','1','--no-access-log'],
                              env=env,stdout=logs,stderr=logs)
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base+'/health',timeout=.2) as response:
                        if response.status==200:break
                except OSError:
                    if proc.poll() is not None:raise AssertionError('Local test server stopped.')
                    time.sleep(.1)
            else:raise AssertionError('Local test server did not start.')
            asyncio.run(check(base,token))
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        logs.seek(0);output=logs.read()
        assert token not in output and 'sig=' not in output
    print('OK: real MCP SDK initialize, tools/list, refused conversion and status over HTTP')


if __name__=='__main__':main()
