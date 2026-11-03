# Generated portable runner; missing checks fail closed.
import json, subprocess, sys
from pathlib import Path
p = json.loads(Path('.codex/quality.json').read_text())
commands = p.get('checks', [])
if not commands or not isinstance(commands, list):
    raise SystemExit('No real project checks configured')
for command in commands:
    if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
        raise SystemExit('Invalid check command')
    result = subprocess.run(command, timeout=120)
    if result.returncode:
        raise SystemExit(result.returncode)
