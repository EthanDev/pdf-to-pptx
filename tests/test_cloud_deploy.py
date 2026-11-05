"""Deployment crash/retry and source-attribution regressions; no cloud calls."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('gcp_deploy',ROOT/'deploy/gcp.py')
gcp=importlib.util.module_from_spec(spec);spec.loader.exec_module(gcp)


class Deployment(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "Source archive check requires Git on host/CI")
    def test_dirty_build_refused_and_clean_build_pins_archive_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'app').mkdir();source=root/'app/main.py';source.write_text('committed source')
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True)
            git('init','-b','feature');git('add','app/main.py')
            git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
            evidence=root/'.quality';evidence.mkdir()
            (root/'.gitignore').write_text('.quality/\n');git('add','.gitignore');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','ignore evidence')
            source.write_text('uncommitted source')
            with patch.object(gcp,'ROOT',root),patch.object(gcp,'EVIDENCE',evidence),patch.object(gcp,'cloud') as cloud:
                with self.assertRaisesRegex(RuntimeError,'Dirty builds'):gcp.build()
                cloud.assert_not_called()
                git('restore','app/main.py')
                def build(*args,**kwargs):
                    self.assertEqual(Path(args[2],'app/main.py').read_text(),'committed source')
                    return {'results':{'images':[{'digest':'sha256:'+'a'*64}]}}
                cloud.side_effect=build;gcp.build()
                value=json.loads((evidence/'image.json').read_text())
                self.assertTrue(value['image'].endswith('@sha256:'+'a'*64));self.assertEqual(value['revision'],git('rev-parse','HEAD').strip())
    def test_versionless_existing_secret_is_repaired_without_rotation(self):
        versions=[];writes=[]
        def cloud(*args,data=None):
            if args[:3]==('secrets','versions','list'):return versions
            if args[:3]==('secrets','versions','add'):
                self.assertEqual(len(data),64);writes.append(True);versions.append({'state':'ENABLED','name':'projects/p/secrets/fixture/versions/1'})
            return {}
        with patch.object(gcp,'cloud',side_effect=cloud):
            gcp.ensure_secret('fixture','api@example.invalid');gcp.ensure_secret('fixture','api@example.invalid')
        self.assertEqual(len(writes),1)
    def test_redeploy_preserves_existing_urls_and_immutable_image(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            state={name:name+'@example.invalid' for name in ('api','worker','invoker','reader','scheduler')}
            state['secret_versions']={'pptx-api-token':'1','pptx-link-key':'1'}
            (evidence/'cloud-resources.json').write_text(json.dumps(state))
            (evidence/'image.json').write_text(json.dumps({'image':'registry/service@sha256:'+'a'*64,'revision':'b'*40}))
            requests=[]
            def cloud(*args,**kwargs):
                if args[:3]==('run','services','describe'):
                    return {'status':{'url':'https://'+args[3]+'.example.run.app'}}
                if args[:2]==('run','deploy'):
                    envfile=next(x.split('=',1)[1] for x in args if x.startswith('--env-vars-file='))
                    values=json.loads(Path(envfile).read_text());requests.append(values)
                    self.assertNotIn('https://unconfigured.run.app',values.values())
                    self.assertTrue(any('@sha256:' in x for x in args))
                return {}
            with patch.object(gcp,'EVIDENCE',evidence),patch.object(gcp,'cloud',side_effect=cloud):gcp.deploy()
            self.assertEqual(len(requests),2)
    def test_enabled_numeric_version_is_pinned_instead_of_disabled_latest(self):
        def cloud(*args,**kwargs):
            if args[:3]==('secrets','versions','list'):
                return [{'name':'projects/p/secrets/fixture/versions/5','state':'ENABLED'}]
            if args[:3]==('secrets','versions','add'):raise AssertionError('Unexpected secret rotation')
            return {}
        with patch.object(gcp,'cloud',side_effect=cloud):
            self.assertEqual(gcp.ensure_secret('fixture','api@example.invalid'),'5')

    def test_denied_lookup_does_not_attempt_create(self):
        with patch.object(gcp,'cloud',side_effect=RuntimeError('PERMISSION_DENIED: resource may not exist')) as cloud:
            with self.assertRaises(RuntimeError):gcp.ensure(['describe'],['create'])
            self.assertEqual(cloud.call_count,1)


if __name__=='__main__':unittest.main()
