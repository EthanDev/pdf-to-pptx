"""Run real offline checks using the project venv when available."""
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
python=root/'.venv/bin/python'
if not python.exists():python=Path(sys.executable)
for test in ('tests/test_all.py','tests/test_hardening.py','tests/test_mcp_client.py','tests/test_cloud_state.py','tests/test_cloud_http.py','tests/test_cloud_deploy.py','tests/test_live_proofs.py'):
    subprocess.run([str(python),'-W','ignore',test],cwd=root,check=True)

if sys.platform == 'linux':
    subprocess.run([str(python),'-W','ignore','tests/test_worker_linux.py'],cwd=root,check=True)
