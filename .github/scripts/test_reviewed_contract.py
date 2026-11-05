"""The compatibility path must reject forged, changed or revoked approval."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from reviewed_contract import validate


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.trusted=root/'trusted';self.candidate=root/'candidate'
        self.head='a'*40;self.paths={'docs/cloud-run-storage-review.md':b'reviewed design','specs/cloud-run-storage/evaluations.json':b'reviewed evaluations'}
        manifest={'source_commit':'b'*40,'artifacts':{p:hashlib.sha256(v).hexdigest() for p,v in self.paths.items()}}
        self.config={'approved_head':self.head,'manifest':'docs/reviews/manifest.json','allowed_paths':['app/','tests/'],'unchanged_guards':['guard.py']}
        for base in (self.trusted,self.candidate):
            data=self.paths|{'docs/reviews/manifest.json':json.dumps(manifest).encode(),
              '.codex/quality-runtime/approval-policy.json':b'{"reviewers":["EthanDev"]}',
              '.codex/quality.json':b'{"human_gates":true}','guard.py':b'trusted guard'}
            for p,v in data.items():(base/p).parent.mkdir(parents=True,exist_ok=True);(base/p).write_bytes(v)
        self.pr={'merged':True,'head':{'sha':self.head}}
        self.reviews=[{'id':1,'submitted_at':'2026-11-05T00:00:00Z','user':{'login':'EthanDev','type':'User'},'state':'APPROVED','commit_id':self.head}]
    def check(self):validate(self.trusted,self.candidate,self.config,self.pr,self.reviews,self.paths,['app/cloud_state.py'])
    def test_real_review_and_same_bytes_accepted(self):self.check()
    def test_unmerged_wrong_head_and_no_review_rejected(self):
        for pr in ({'merged':False,'head':{'sha':self.head}},{'merged':True,'head':{'sha':'c'*40}}):
            self.pr=pr
            with self.assertRaises(ValueError):self.check()
        self.pr={'merged':True,'head':{'sha':self.head}};self.reviews=[]
        with self.assertRaises(ValueError):self.check()
    def test_bot_dismissal_and_wrong_commit_rejected(self):
        for mutate in ('bot','dismissed','wrong-head'):
            item={'id':1,'user':{'login':'EthanDev','type':'User'},'state':'APPROVED','commit_id':self.head}
            if mutate=='bot':item['user']['type']='Bot'
            if mutate=='dismissed':item['state']='DISMISSED'
            if mutate=='wrong-head':item['commit_id']='c'*40
            self.reviews=[item]
            with self.assertRaises(ValueError):self.check()
    def test_changed_contract_guard_or_disabled_human_gates_rejected(self):
        for path,contents in [('docs/cloud-run-storage-review.md',b'altered'),('guard.py',b'weakened'),('.codex/quality.json',b'{"human_gates":false}')]:
            old=(self.candidate/path).read_bytes();(self.candidate/path).write_bytes(contents)
            with self.assertRaises(ValueError):self.check()
            (self.candidate/path).write_bytes(old)
    def test_production_policy_preserves_compatibility_guard(self):
        policy=json.loads((Path(__file__).resolve().parents[1]/'reviewed-contract.json').read_text())
        required=('.github/workflows/codex-project-sdlc.yml','.github/scripts/reviewed_contract.py','.github/reviewed-contract.json')
        for path in required:
            for root in (self.trusted,self.candidate):
                (root/path).parent.mkdir(parents=True,exist_ok=True);(root/path).write_bytes(b'trusted')
            self.config['unchanged_guards']=list(policy['unchanged_guards'])
            for other in self.config['unchanged_guards']:
                for root in (self.trusted,self.candidate):
                    (root/other).parent.mkdir(parents=True,exist_ok=True)
                    if not (root/other).exists():(root/other).write_bytes(b'trusted')
            (self.candidate/path).write_bytes(b'weakened')
            with self.assertRaises(ValueError):self.check()
            (self.candidate/path).write_bytes(b'trusted')
        self.config['allowed_paths']=policy['allowed_paths']
        with self.assertRaises(ValueError):
            validate(self.trusted,self.candidate,self.config,self.pr,self.reviews,self.paths,['.github/workflows/unreviewed.yml'])

    def test_unexpected_scope_rejected(self):
        self.config['allowed_paths']=[]
        with self.assertRaises(ValueError):self.check()


if __name__=='__main__':unittest.main()
