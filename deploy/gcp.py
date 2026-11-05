"""Idempotent setup for this service's separately approved Google Cloud project."""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import io
import tarfile
import tempfile

PROJECT = 'pdf-to-pptx-261103-7ec3'
ACCOUNT = 'ethan@cashew.ai'
REGION = 'europe-west1'
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT/'.quality/deployment'


def cloud(*args, data=None):
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--account='+ACCOUNT,
                             '--quiet', '--format=json'], input=data, capture_output=True, text=True)
    if result.returncode:
        # Commands never contain secret values; secret payloads use stdin only.
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def ensure(describe, create):
    try:
        return cloud(*describe)
    except RuntimeError as error:
        if any(mark in str(error).lower() for mark in ('permission', 'reauthentication', 'unauthenticated')):
            raise
        if not any(mark in str(error).lower() for mark in ('not_found', 'not found', 'does not exist', 'notfound', 'not_found')):
            raise
    return cloud(*create)


def identity(name):
    email=name+'@'+PROJECT+'.iam.gserviceaccount.com'
    ensure(['iam','service-accounts','describe',email], ['iam','service-accounts','create',name])
    return email


def role(member, name):
    cloud('projects','add-iam-policy-binding',PROJECT,'--member=serviceAccount:'+member,
          '--role='+name,'--condition=None')


def ensure_secret(name, api):
    ensure(['secrets','describe',name], ['secrets','create',name,'--replication-policy=automatic'])
    versions=cloud('secrets','versions','list',name,'--filter=state=ENABLED','--sort-by=~createTime','--limit=1')
    if not versions:
        cloud('secrets','versions','add',name,'--data-file=-',data=secrets.token_hex(32))
        versions=cloud('secrets','versions','list',name,'--filter=state=ENABLED','--sort-by=~createTime','--limit=1')
    if not versions:
        raise RuntimeError('Secret initialization did not produce an enabled version.')
    cloud('secrets','add-iam-policy-binding',name,'--member=serviceAccount:'+api,'--role=roles/secretmanager.secretAccessor')
    return versions[0]['name'].rsplit('/',1)[-1]


def prepare():
    project=cloud('projects','describe',PROJECT)
    assert project['projectNumber']=='497674931375', 'Unexpected project identity'
    if project.get('parent'):
        raise RuntimeError('Inherited organization logging/IAM must be inspected before release.')
    accounts=cloud('billing','accounts','list','--filter=open=true')
    if not cloud('billing','projects','describe',PROJECT)['billingEnabled']:
        if len(accounts)!=1:
            raise RuntimeError('Choose a billing account; multiple accounts are available.')
        cloud('billing','projects','link',PROJECT,'--billing-account='+accounts[0]['name'].split('/')[-1])
    apis=['run','firestore','storage','cloudtasks','cloudscheduler','secretmanager','artifactregistry','cloudbuild','iamcredentials','logging','monitoring']
    cloud('services','enable',*[name+'.googleapis.com' for name in apis])
    ensure(['firestore','databases','describe','--database=(default)'],
           ['firestore','databases','create','--database=(default)','--location='+REGION,'--type=firestore-native'])
    api,worker,invoker,reader,scheduler,builder=[identity('pptx-'+name) for name in ('api','worker','task','reader','scheduler','build')]
    for suffix in ('results','build'):
        bucket='gs://'+PROJECT+'-'+suffix
        ensure(['storage','buckets','describe',bucket],
               ['storage','buckets','create',bucket,'--location='+REGION,'--uniform-bucket-level-access',
                '--public-access-prevention','--soft-delete-duration=0'])
        cloud('storage','buckets','update',bucket,'--no-versioning','--soft-delete-duration=0')
    lifecycle=EVIDENCE/'lifecycle.json'
    lifecycle.write_text(json.dumps({'rule':[{'action':{'type':'Delete'},'condition':{'age':8}}]}))
    cloud('storage','buckets','update','gs://'+PROJECT+'-results','--lifecycle-file='+str(lifecycle))
    cloud('storage','buckets','add-iam-policy-binding','gs://'+PROJECT+'-results',
          '--member=serviceAccount:'+api,'--role=roles/storage.objectUser')
    for name,permissions in [('pptxSigner','iam.serviceAccounts.signBlob'),('pptxFinalReader','storage.objects.get')]:
        ensure(['iam','roles','describe',name], ['iam','roles','create',name,'--title='+name,'--permissions='+permissions,'--stage=GA'])
    cloud('iam','service-accounts','add-iam-policy-binding',reader,
          '--member=serviceAccount:'+api,'--role=projects/'+PROJECT+'/roles/pptxSigner')
    condition="expression=resource.name.startsWith('projects/_/buckets/"+PROJECT+"-results/objects/final/'),title=final_objects_only"
    cloud('storage','buckets','add-iam-policy-binding','gs://'+PROJECT+'-results',
          '--member=serviceAccount:'+reader,'--role=projects/'+PROJECT+'/roles/pptxFinalReader','--condition='+condition)
    role(api,'roles/datastore.user');role(api,'roles/cloudtasks.enqueuer')
    cloud('iam','service-accounts','add-iam-policy-binding',invoker,
          '--member=serviceAccount:'+api,'--role=roles/iam.serviceAccountUser')
    ensure(['tasks','queues','describe','convert','--location='+REGION],
           ['tasks','queues','create','convert','--location='+REGION])
    cloud('tasks','queues','update','convert','--location='+REGION,'--max-concurrent-dispatches=2',
          '--max-dispatches-per-second=5','--max-attempts=100','--max-retry-duration=1800s',
          '--min-backoff=30s','--max-backoff=300s','--log-sampling-ratio=0')
    ensure(['artifacts','repositories','describe','pptx','--location='+REGION],
           ['artifacts','repositories','create','pptx','--location='+REGION,'--repository-format=docker'])
    cloud('artifacts','repositories','add-iam-policy-binding','pptx','--location='+REGION,
          '--member=serviceAccount:'+builder,'--role=roles/artifactregistry.writer')
    cloud('storage','buckets','add-iam-policy-binding','gs://'+PROJECT+'-build',
          '--member=serviceAccount:'+builder,'--role=roles/storage.objectViewer')
    role(builder,'roles/logging.logWriter')
    secret_versions={name:ensure_secret(name,api) for name in ('pptx-api-token','pptx-link-key')}
    # Query credentials on compatibility download routes must not be retained or
    # exported by project sinks. Direct Storage links avoid this request path.
    sinks=cloud('logging','sinks','list')
    if any(sink['name'] not in ('_Default','_Required') for sink in sinks):
        raise RuntimeError('Review additional log exports before configuring this service.')
    iam=cloud('projects','get-iam-policy',PROJECT)
    if iam.get('auditConfigs'):
        raise RuntimeError('Review existing audit logging before signed downloads are enabled.')
    sink=cloud('logging','sinks','describe','_Default')
    flag='--update-exclusion=' if any(e['name']=='pptx-request-privacy' for e in sink.get('exclusions',[])) else '--add-exclusion='
    cloud('logging','sinks','update','_Default',flag+'name=pptx-request-privacy,filter=resource.type="cloud_run_revision" AND log_id("run.googleapis.com/requests")')
    state={'project':PROJECT,'region':REGION,'api':api,'worker':worker,'invoker':invoker,
           'reader':reader,'scheduler':scheduler,'builder':builder,'secret_versions':secret_versions}
    (EVIDENCE/'cloud-resources.json').write_text(json.dumps(state,indent=2)+'\n')
    print('Prepared isolated service resources. Worker identity was granted no roles.')


def build():
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=ROOT,text=True).strip():
        raise RuntimeError('Commit the source before building. Dirty builds are refused.')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    tag=REGION+'-docker.pkg.dev/'+PROJECT+'/pptx/service:'+revision
    config=EVIDENCE/'build.json'
    config.write_text(json.dumps({'steps':[{'name':'gcr.io/cloud-builders/docker','args':['build','-t',tag,
          '--build-arg','SOURCE_REVISION='+revision,'.']}], 'images':[tag],
          'serviceAccount':'projects/'+PROJECT+'/serviceAccounts/pptx-build@'+PROJECT+'.iam.gserviceaccount.com',
          'options':{'logging':'CLOUD_LOGGING_ONLY'}}))
    archive=subprocess.check_output(['git','archive',revision],cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='pptx-source-') as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(directory,filter='data')
        result=cloud('builds','submit',directory,'--config='+str(config),'--region='+REGION,
                     '--gcs-source-staging-dir=gs://'+PROJECT+'-build/source')
    digest=result['results']['images'][0]['digest']
    if not digest.startswith('sha256:') or len(digest)!=71:
        raise RuntimeError('Build did not return an image digest.')
    image=tag.rsplit(':',1)[0]+'@'+digest
    (EVIDENCE/'build-result.json').write_text(json.dumps(result,indent=2)+'\n')
    (EVIDENCE/'image.json').write_text(json.dumps({'image':image,'revision':revision},indent=2)+'\n')
    print('Built pinned image for source '+revision)


def deploy():
    state=json.loads((EVIDENCE/'cloud-resources.json').read_text())
    descriptor=json.loads((EVIDENCE/'image.json').read_text())
    image,revision=descriptor['image'],descriptor['revision']
    common=['run','deploy']
    # First create the private worker with a non-working coordinator URL. No
    # task is enqueued until the public API is configured and checked.
    def current_url(name):
        try:return cloud('run','services','describe',name,'--region='+REGION)['status']['url']
        except RuntimeError as error:
            if not any(mark in str(error).lower() for mark in ('not_found','not found')):raise
            return 'https://unconfigured.run.app'
    previous_api=current_url('pdf-to-pptx-api')
    worker_env={'COORDINATOR_URL':previous_api,'SOURCE_REVISION':revision}
    env=EVIDENCE/'worker-env.json';env.write_text(json.dumps(worker_env))
    args=['cloud_worker:app','--app-dir','app','--host','0.0.0.0','--port','8080','--workers','1','--no-access-log']
    cloud(*common,'pdf-to-pptx-worker','--region='+REGION,'--image='+image,'--service-account='+state['worker'],
          '--no-allow-unauthenticated','--memory=4Gi','--cpu=2','--concurrency=1','--max-instances=2',
          '--min-instances=0','--timeout=600','--execution-environment=gen2','--command=uvicorn','--args='+','.join(args),'--env-vars-file='+str(env))
    worker=cloud('run','services','describe','pdf-to-pptx-worker','--region='+REGION)['status']['url']
    cloud('run','services','add-iam-policy-binding','pdf-to-pptx-worker','--region='+REGION,
          '--member=serviceAccount:'+state['invoker'],'--role=roles/run.invoker')
    api_env={'JOB_BACKEND':'gcp','GOOGLE_CLOUD_PROJECT':PROJECT,'RESULT_BUCKET':PROJECT+'-results',
             'DOWNLOAD_SIGNER':state['reader'],'CLOUD_REGION':REGION,'TASK_QUEUE':'convert','WORKER_URL':worker,
             'TASK_INVOKER':state['invoker'],'MAINTENANCE_IDENTITY':state['scheduler'],'SERVICE_URL':previous_api,
             'PUBLIC_URL':previous_api,'SOURCE_REVISION':revision,
             'SOURCE_URL':'https://github.com/EthanDev/pdf-to-pptx/tree/'+revision}
    env_api=EVIDENCE/'api-env.json';env_api.write_text(json.dumps(api_env))
    cloud(*common,'pdf-to-pptx-api','--region='+REGION,'--image='+image,'--service-account='+state['api'],
          '--allow-unauthenticated','--memory=1Gi','--cpu=1','--concurrency=20','--max-instances=3',
          '--min-instances=0','--timeout=600','--execution-environment=gen2','--env-vars-file='+str(env_api),
          '--set-secrets=API_TOKEN=pptx-api-token:'+state['secret_versions']['pptx-api-token']+',LINK_KEY=pptx-link-key:'+state['secret_versions']['pptx-link-key'])
    api=cloud('run','services','describe','pdf-to-pptx-api','--region='+REGION)['status']['url']
    cloud('run','services','update','pdf-to-pptx-api','--region='+REGION,'--update-env-vars=PUBLIC_URL='+api+',SERVICE_URL='+api)
    cloud('run','services','update','pdf-to-pptx-worker','--region='+REGION,'--update-env-vars=COORDINATOR_URL='+api)
    ensure(['scheduler','jobs','describe','pptx-maintenance','--location='+REGION],
           ['scheduler','jobs','create','http','pptx-maintenance','--location='+REGION,'--schedule=* * * * *',
            '--uri='+api+'/internal/maintenance','--http-method=POST',
            '--oidc-service-account-email='+state['scheduler'],'--oidc-token-audience='+api,
            '--attempt-deadline=300s'])
    state.update(api_url=api,worker_url=worker,source_revision=revision,image=image)
    (EVIDENCE/'cloud-resources.json').write_text(json.dumps(state,indent=2)+'\n')
    print('Cloud Run services configured: '+api+'/mcp')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','build','deploy'])
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    {'prepare':prepare,'build':build,'deploy':deploy}[parser.parse_args().action]()
