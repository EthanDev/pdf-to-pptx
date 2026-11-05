"""Recognize one real reviewed contract; never infer approval from local fields.

CI loads this checker, config, manifest and reviewer policy from the protected
base. Native artifact checks remain the fallback for every unmarked change.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def approved(reviews, allowed, head):
    latest={}
    for review in sorted(reviews,key=lambda r:(r.get('submitted_at') or '',r.get('id',0))):
        user=review.get('user',{})
        if user.get('type')=='User' and user.get('login') in allowed and review.get('state') in ('APPROVED','CHANGES_REQUESTED','DISMISSED'):
            latest[user['login']]=review
    return not any(r['state']=='CHANGES_REQUESTED' for r in latest.values()) and any(
        r['state']=='APPROVED' and r.get('commit_id')==head for r in latest.values())


def validate(trusted,candidate,config,source_pr,reviews,reviewed_bytes,changed):
    head=config['approved_head']
    if not re.fullmatch(r'[0-9a-f]{40}',head) or not source_pr.get('merged') or source_pr.get('head',{}).get('sha')!=head:
        raise ValueError('The referenced contract PR/head is not merged.')
    policy=json.loads((trusted/'.codex/quality-runtime/approval-policy.json').read_text())
    if not approved(reviews,set(policy['reviewers']),head):
        raise ValueError('The exact contract head has no valid trusted-human approval.')
    manifest_path=config['manifest']
    if (candidate/manifest_path).read_bytes()!=(trusted/manifest_path).read_bytes():
        raise ValueError('The candidate changed the trusted approval manifest.')
    manifest=json.loads((trusted/manifest_path).read_text())
    if not re.fullmatch(r'[0-9a-f]{40}',manifest['source_commit']):
        raise ValueError('Invalid contract source reference.')
    for name,digest in manifest['artifacts'].items():
        if name not in ('docs/cloud-run-storage-review.md','specs/cloud-run-storage/evaluations.json'):
            raise ValueError('Unexpected contract artifact.')
        for contents in ((trusted/name).read_bytes(),(candidate/name).read_bytes(),reviewed_bytes[name]):
            if hashlib.sha256(contents).hexdigest()!=digest:
                raise ValueError('Approved contract/evaluation bytes changed.')
    for path in config['unchanged_guards']:
        if (candidate/path).read_bytes()!=(trusted/path).read_bytes():
            raise ValueError('Approval guard changes need their own review.')
    if json.loads((candidate/'.codex/quality.json').read_text()).get('human_gates') is not True:
        raise ValueError('Human gates must remain enabled.')
    for path in changed:
        if not any(path.startswith(rule) if rule.endswith('/') else path==rule for rule in config['allowed_paths']):
            raise ValueError('A changed path is outside the reviewed contract scope.')


def main():
    parser=argparse.ArgumentParser()
    for field in ('trusted','candidate','repo','pr','base','branch','body-file'):
        parser.add_argument('--'+field,required=True)
    parser.add_argument('--labels',default='')
    args=parser.parse_args();trusted=Path(args.trusted).resolve();candidate=Path(args.candidate).resolve()
    config=json.loads((trusted/'.github/reviewed-contract.json').read_text())
    body=Path(args.body_file).read_text()
    if config['marker'] not in body.splitlines():
        return subprocess.call([sys.executable,str(trusted/'.codex/quality-runtime/sdlc_gate_check.py'),
            '--base',args.base,'--branch',args.branch,'--labels',args.labels,'--body-file',args.body_file,
            '--config',str(trusted/'.claude/sdlc.json')],cwd=candidate)
    try:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',args.repo) or not args.pr.isdigit():
            raise ValueError('Invalid repository/PR reference.')
        def api(path):
            return json.loads(subprocess.check_output(['gh','api',path],text=True,timeout=30))
        source=api(f'repos/{args.repo}/pulls/{config["approval_pr"]}')
        pages=json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',
            f'repos/{args.repo}/pulls/{config["approval_pr"]}/reviews'],text=True,timeout=30))
        reviews=[review for page in pages for review in page]
        current=api(f'repos/{args.repo}/pulls/{args.pr}')
        actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=candidate,text=True).strip()
        if current['head']['sha']!=actual:
            raise ValueError('Candidate differs from the current PR head.')
        manifest=json.loads((trusted/config['manifest']).read_text())
        reviewed={path:subprocess.check_output(['git','show',config['approved_head']+':'+path],cwd=candidate)
                  for path in manifest['artifacts']}
        changed=subprocess.check_output(['git','diff','--name-only',args.base,'HEAD'],cwd=candidate,text=True).splitlines()
        validate(trusted,candidate,config,source,reviews,reviewed,changed)
        print('PASS: exact unchanged contract has authenticated human review in merged PR #1; current-head source approval remains separately required.')
        return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        print('FAIL: cannot verify the exact reviewed contract, scope or guards; no approval inferred.')
        return 1


if __name__=='__main__':sys.exit(main())
