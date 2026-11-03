#!/usr/bin/env python3
"""Validate G1–G3 against GitHub reviews, not Git author names or Markdown claims.

CI must supply a trusted policy/checker and use restricted builder credentials.
The pure check() boundary accepts API records for tests; the CLI always fetches
them from GitHub and never accepts an agent-written receipt file.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def status(path):
    text = path.read_text() if path.exists() else ''
    front = re.match(r'^---\s*\n(.*?)\n---', text, re.S)
    match = re.search(r'^status:\s*[\"\x27]?([\w-]+)', front[1], re.M) if front else None
    return match[1] if match else ''


def check(root, policy, reviews, head=None, change=None, require_plan=False):
    errors = []
    allowed = set(policy.get('reviewers', []))
    if not allowed:
        return ['No trusted human reviewers configured']
    latest = {}
    for review in sorted(reviews, key=lambda item: (item.get('submitted_at') or '', item.get('id', 0))):
        user = review.get('user', {})
        if user.get('login') in allowed and user.get('type') == 'User':
            if review.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
                latest[user['login']] = review
    approvals = [item for item in latest.values() if item.get('state') == 'APPROVED']
    if policy.get('require_pr_approval'):
        if head is None:
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        if not any(item.get('commit_id') == head for item in approvals):
            errors.append('Current PR head has no authenticated approval from a trusted human')
    if any(item.get('state') == 'CHANGES_REQUESTED' for item in latest.values()):
        errors.append('An authorized reviewer requested changes')
    gated = {'intent': {'accepted', 'rejected'}, 'spec': {'approved'},
             'plan': {'accepted', 'building', 'done'}}
    folders = sorted((root / 'sdlc/changes').glob('*'))
    if change:
        folders = [folder for folder in folders if folder.name.split('-')[0] == change]
        if len(folders) != 1:
            return errors + ['The current change must resolve to exactly one approved change folder']
    if require_plan and (not folders or any(status(folder / 'plan.md') not in ('accepted', 'building', 'done') for folder in folders)):
        errors.append('Local implementation requires an accepted plan for the current change')
    for folder in folders:
        if not folder.is_dir():
            continue
        states = {kind: status(folder / f'{kind}.md') for kind in gated}
        if states['spec'] == 'approved' and states['intent'] != 'accepted':
            errors.append(f'{folder.name}: G2 requires accepted G1')
        if states['plan'] in gated['plan']:
            if states['intent'] != 'accepted':
                errors.append(f'{folder.name}: G3 requires accepted G1')
            if (folder / 'spec.md').exists() and states['spec'] != 'approved':
                errors.append(f'{folder.name}: G3 requires approved G2')
        for kind, accepted in gated.items():
            path = folder / f'{kind}.md'
            if states[kind] not in accepted:
                continue
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            if kind == 'spec' and re.search(rb'(?im)^\s*[-*]?\s*\**Decision:?\**:?\s*pending', data):
                errors.append(f'{relative}: a design decision is pending')
            verified = False
            for review in approvals:
                commit = review.get('commit_id', '')
                if not re.fullmatch(r'[a-fA-F0-9]{40,64}', commit):
                    continue
                result = subprocess.run(['git', 'show', f'{commit}:{relative}'], cwd=root, capture_output=True)
                if result.returncode == 0 and hashlib.sha256(result.stdout).digest() == hashlib.sha256(data).digest():
                    verified = True
                    break
            if not verified:
                errors.append(f'{relative}: no authenticated human approval for this exact artifact')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path('.'))
    parser.add_argument('--policy', type=Path, required=True, help='Policy from trusted base branch, not PR contents')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--pr', type=int, required=True)
    parser.add_argument('--artifact-only', action='store_true', help='Local G1-G3 check only; never used by the required PR workflow')
    parser.add_argument('--change')
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo) or args.pr < 1:
            raise ValueError('invalid GitHub repository or PR number')
        response = subprocess.run(['gh', 'api', '--paginate', '--slurp',
                                   f'repos/{args.repo}/pulls/{args.pr}/reviews'],
                                  capture_output=True, text=True, timeout=30, check=True)
        reviews = [item for page in json.loads(response.stdout) for item in page]
        policy = json.loads(args.policy.read_text())
        if args.artifact_only:
            if not args.change or not re.fullmatch(r'\d{3}', args.change):
                raise ValueError('artifact checks require a three-digit change ID')
            policy['require_pr_approval'] = False
        errors = check(args.project.resolve(), policy, reviews, change=args.change, require_plan=args.artifact_only)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        errors = ['Cannot verify authenticated approvals; fail closed (check GitHub access and trusted policy)']
    print(json.dumps({'status': 'fail' if errors else 'pass', 'errors': errors}, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
