"""Real Linux native-child death/watchdog/cancellation checks, run in the image."""
import asyncio
import ctypes
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
import cloud_worker


def native_child(pidfile, expected_parent):
    cloud_worker.parent_death(expected_parent)
    Path(pidfile).write_text(str(os.getpid()))
    ctypes.CDLL(None).sleep(60)


def helper(directory, alarm, expected_parent):
    os.setsid();cloud_worker.parent_death(expected_parent)
    signal.signal(signal.SIGALRM,signal.SIG_DFL)
    child=multiprocessing.get_context('spawn').Process(target=native_child,args=(str(Path(directory)/'child'),os.getpid()))
    child.start()
    if alarm:
        deadline=time.monotonic()+30
        while not (Path(directory)/'child').exists() and time.monotonic()<deadline:time.sleep(.05)
        signal.alarm(1)
    ctypes.CDLL(None).sleep(60)


def cancel_attempt(jid,credential,coordinator,directory,expected_parent):
    os.setsid();cloud_worker.parent_death(expected_parent);ctypes.CDLL(None).sleep(60)


def main():
    if sys.platform!='linux':
        print('SKIP: Linux process guarantees must be tested in Docker');return
    # Reap orphan grandchildren rather than rely on a container PID1 init.
    assert ctypes.CDLL(None).prctl(36,1)==0
    with tempfile.TemporaryDirectory() as directory:
        for alarm in (False,True):
            path=Path(directory)/'child';path.unlink(missing_ok=True)
            parent=subprocess.Popen([sys.executable,__file__,'helper',directory,'1' if alarm else '0',str(os.getpid())])
            try:
                deadline=time.monotonic()+30
                while not path.exists() and time.monotonic()<deadline:time.sleep(.05)
                assert path.exists(),'Native child did not start'
                child=int(path.read_text())
                if not alarm:parent.kill()
                assert parent.wait(timeout=5)<0
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    pid,status=os.waitpid(child,os.WNOHANG)
                    if pid:
                        assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL
                        break
                    time.sleep(.05)
                else:raise AssertionError('Native child survived parent death')
            finally:
                if parent.poll() is None:parent.kill();parent.wait()
    async def cancel():
        cloud_worker.attempt=cancel_attempt
        os.environ['COORDINATOR_URL']='https://coordinator.example.run.app'
        task=asyncio.create_task(cloud_worker.execute(cloud_worker.Delivery(job_id='a'*12,credential='b'*64)))
        await asyncio.sleep(1);task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        assert not multiprocessing.active_children(),'Cancelled request leaked an attempt process'
    asyncio.run(cancel())
    print('OK: Linux native child parent-death, hard watchdog and request cancellation')


if __name__=='__main__':
    if len(sys.argv)>1:helper(sys.argv[2],sys.argv[3]=='1',int(sys.argv[4]))
    else:main()
