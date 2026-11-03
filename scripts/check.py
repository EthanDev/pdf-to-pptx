"""Run real offline checks using the project venv when available."""
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
python=root/'.venv/bin/python'
if not python.exists():python=Path(sys.executable)
for test in ('tests/test_all.py','tests/test_hardening.py','tests/test_mcp_client.py'):
    subprocess.run([str(python),'-W','ignore',test],cwd=root,check=True)
