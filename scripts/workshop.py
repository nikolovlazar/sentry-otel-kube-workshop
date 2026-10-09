#!/usr/bin/env python3
"""Explicitly scoped local cluster commands. Never changes global kubeconfig or Docker context."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import re
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'.local';BIN=LOCAL/'bin';GENERATED=LOCAL/'generated';KUBECONFIG=LOCAL/'kubeconfig'
CLUSTER='sentry-otel-workshop';CONTEXT='kind-'+CLUSTER;APP=CLUSTER;OBS='observability'
CHART_DIGEST='be034275b55deeddd6b7bc1f4da6eeb02b8efc418a4a29e2b1977851b24b1e63'
CHART=LOCAL/'downloads/gateway-helm-v1.9.2.tgz'
CHART_SHA256='1079cad009e0885f6e10e5f712257d8e5fdaf911d2ceb3fa1b78632c8f29bbf9'
COMPONENTS={'web':'apps/web','catalog':'services/catalog','fulfillment':'services/fulfillment','notification-worker':'services/notification-worker'}

def config():
    result={}
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            if '=' not in line:raise SystemExit('Invalid .env line; use KEY=value.')
            key,value=line.split('=',1)
            if value.startswith(('"',"'")) and value[-1:] == value[:1]:value=value[1:-1]
            result[key.strip()]=value
    for key in list(result):
        if key in os.environ:result[key]=os.environ[key]
    result.setdefault('DOCKER_CONTEXT','colima')
    result.setdefault('WORKSHOP_ENVIRONMENT','workshop-local')
    result.setdefault('WORKSHOP_SESSION_ID','local-workshop')
    result.setdefault('SENTRY_EXPORTER_MODE','sentry')
    revision=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=False)
    version=revision.stdout.strip() if revision.returncode==0 else 'dev-uncommitted'
    result.setdefault('SERVICE_VERSION',os.environ.get('SERVICE_VERSION',version))
    result.setdefault('SENTRY_RELEASE',os.environ.get('SENTRY_RELEASE','sentry-otel-workshop@'+result['SERVICE_VERSION']))
    return result

CFG=config()
ENV={**os.environ,**CFG,'PATH':str(BIN)+os.pathsep+os.environ.get('PATH',''),'KIND_EXPERIMENTAL_PROVIDER':'docker','KUBECONFIG':str(KUBECONFIG)}

def run(args,capture=False,input=None,check=True,env=None):
    return subprocess.run([str(x) for x in args],cwd=ROOT,env=env or ENV,text=True,input=input,stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else None,check=check)

def guarded():
    if not KUBECONFIG.exists():raise SystemExit('Workshop kubeconfig missing. Run make cluster-up.')
    check=run([BIN/'kubectl','--kubeconfig',KUBECONFIG,'--context',CONTEXT,'config','view','--minify','-o','json'],capture=True)
    settings=json.loads(check.stdout)
    if settings['contexts'][0]['name'] != CONTEXT:raise SystemExit('Refusing unexpected Kubernetes context.')
    server=settings['clusters'][0]['cluster']['server']
    if urlparse(server).hostname not in ('127.0.0.1','localhost'):raise SystemExit('Refusing a non-local Kubernetes API server.')

def kc(*args,capture=False,input=None,check=True):
    guarded()
    return run([BIN/'kubectl','--kubeconfig',KUBECONFIG,'--context',CONTEXT,*args],capture,input,check)

def apply_file(path):kc('apply','-f',path)
def apply_obj(obj):kc('apply','-f','-',input=json.dumps(obj))
def private_write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value);path.chmod(0o600)

def require(keys):
    missing=[key for key in keys if not CFG.get(key) or CFG[key].startswith(('replace-with','your-'))]
    if missing:raise SystemExit('Configure .env: '+', '.join(missing))

def doctor():
    required={'kind':'v0.33.0','kubectl':'v1.36.4','helm':'v4.3.0','node':'v24.21.0'}
    flags={'kind':['version'],'kubectl':['version','--client','-o','json'],'helm':['version','--short'],'node':['--version']}
    for tool,wanted in required.items():
        if not (BIN/tool).exists():raise SystemExit('Missing local '+tool+'. Run make bootstrap.')
        result=run([BIN/tool,*flags[tool]],capture=True).stdout
        if wanted not in result:raise SystemExit(f'Expected {tool} {wanted}; run make bootstrap with a clean local tool.')
        print(f'{tool} {wanted}')
    info=json.loads(run(['docker','--context',CFG['DOCKER_CONTEXT'],'info','--format','{{json .}}'],capture=True).stdout)
    if info.get('ServerErrors'):raise SystemExit('Docker daemon unavailable in configured context.')
    cpus=info['NCPU'];memory=info['MemTotal']/1024**3
    print(f'Docker context {CFG["DOCKER_CONTEXT"]}: {cpus} CPUs, {memory:.1f} GiB')
    if cpus<4 or memory<7.5:raise SystemExit('Workshop needs at least 4 CPUs and 8 GiB VM memory.')
    if CFG['SENTRY_EXPORTER_MODE'] not in ('sentry','otlphttp'):raise SystemExit('SENTRY_EXPORTER_MODE must be sentry or otlphttp.')
    free=shutil.disk_usage(ROOT).free/1024**3
    print(f'Host workspace filesystem: {free:.1f} GiB free; reserve at least 20 GiB in the container VM too.')
    if free<20:raise SystemExit('At least 20 GiB free workspace disk is required for builds and tools.')
    with socket.socket() as sock:
        occupied=sock.connect_ex(('127.0.0.1',8080))==0
    if occupied:
        state=LOCAL/'port-forward.json'
        if not state.exists():raise SystemExit('localhost8080 is occupied by another process.')
        pid=json.loads(state.read_text())['pid']
        cmd=run(['ps','-p',str(pid),'-o','command='],capture=True,check=False).stdout
        if str(KUBECONFIG) not in cmd or 'port-forward' not in cmd:raise SystemExit('localhost8080 is not the workshop managed forward.')
    print('localhost8080 available or owned by the workshop forward')
    require(['POSTGRES_PASSWORD','CATALOG_DB_PASSWORD','FULFILLMENT_DB_PASSWORD','REDIS_PASSWORD','SENTRY_ORG_SLUG','SENTRY_BASE_URL']+['SENTRY_DSN_'+x for x in ['WEB','CATALOG','FULFILLMENT','WORKER','INFRASTRUCTURE']])
    if CFG['SENTRY_EXPORTER_MODE']=='sentry':require(['SENTRY_AUTH_TOKEN'])
    for suffix in ['WEB','CATALOG','FULFILLMENT','WORKER','INFRASTRUCTURE']:
        dsn=urlparse(CFG['SENTRY_DSN_'+suffix])
        if dsn.scheme!='https' or not dsn.hostname or not dsn.username or not re.fullmatch(r'\d+',dsn.path.strip('/')):raise SystemExit('Invalid project HTTPS Sentry DSN for '+suffix)
    print('Private .env contains valid project DSN shapes and required credentials (values hidden)')

def cluster_up():
    doctor()
    clusters=run([BIN/'kind','get','clusters'],capture=True).stdout.splitlines()
    if CLUSTER in clusters:
        run([BIN/'kind','export','kubeconfig','--name',CLUSTER,'--kubeconfig',KUBECONFIG])
    else:
        run([BIN/'kind','create','cluster','--name',CLUSTER,'--kubeconfig',KUBECONFIG,'--config',ROOT/'infra/kind.yaml','--wait','120s'])
    KUBECONFIG.chmod(0o600)
    kc('wait','--for=condition=Ready','nodes','--all','--timeout=120s')
    private_write(LOCAL/'cluster.json',json.dumps({'cluster':CLUSTER,'context':CONTEXT,'docker_context':CFG['DOCKER_CONTEXT']},indent=2))

def secrets():
    require(['POSTGRES_PASSWORD','CATALOG_DB_PASSWORD','FULFILLMENT_DB_PASSWORD','REDIS_PASSWORD','SENTRY_ORG_SLUG','SENTRY_BASE_URL']+['SENTRY_DSN_'+x for x in ['WEB','CATALOG','FULFILLMENT','WORKER','INFRASTRUCTURE']])
    if CFG['SENTRY_EXPORTER_MODE']=='sentry':require(['SENTRY_AUTH_TOKEN'])
    data={k:v for k,v in CFG.items() if k.startswith('SENTRY_') or k in ['WORKSHOP_SESSION_ID','WORKSHOP_ENVIRONMENT','SERVICE_VERSION','POSTGRES_PASSWORD','REDIS_PASSWORD']}
    data['CATALOG_DATABASE_URL']='postgresql://catalog:'+quote(CFG['CATALOG_DB_PASSWORD'],safe='')+'@postgres:5432/workshop'
    data['FULFILLMENT_DATABASE_URL']='postgresql://fulfillment:'+quote(CFG['FULFILLMENT_DB_PASSWORD'],safe='')+'@postgres:5432/workshop'
    data['REDIS_URL']='redis://:'+quote(CFG['REDIS_PASSWORD'],safe='')+'@redis:6379/0'
    gateway={k:v for k,v in data.items() if k.startswith('SENTRY_') or k.startswith('WORKSHOP_') or k=='SERVICE_VERSION'}
    for suffix in ['WEB','CATALOG','FULFILLMENT','WORKER','INFRASTRUCTURE']:
        dsn=urlparse(CFG['SENTRY_DSN_'+suffix]);project=dsn.path.strip('/')
        if not dsn.username or not project or dsn.scheme!='https':raise SystemExit('Invalid HTTPS Sentry DSN for '+suffix)
        gateway['SENTRY_OTLP_ENDPOINT_'+suffix]=f'https://{dsn.hostname}/api/{project}/integration/otlp'
        gateway['SENTRY_OTLP_AUTH_'+suffix]='Sentry sentry_version=7,sentry_key='+dsn.username
    # Source-map credentials are never deployed to application/collector pods.
    for d in [data,gateway]:d.pop('SENTRY_SOURCE_MAP_AUTH_TOKEN',None)
    data.pop('SENTRY_AUTH_TOKEN',None)
    def secret(name,ns,values):return {'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':ns},'type':'Opaque','stringData':values}
    sqlquote=lambda v:"'"+v.replace("'","''")+"'"
    init='CREATE ROLE catalog LOGIN PASSWORD '+sqlquote(CFG['CATALOG_DB_PASSWORD'])+';\nCREATE ROLE fulfillment LOGIN PASSWORD '+sqlquote(CFG['FULFILLMENT_DB_PASSWORD'])+';\nCREATE SCHEMA catalog AUTHORIZATION catalog;\nCREATE SCHEMA fulfillment AUTHORIZATION fulfillment;\nREVOKE CREATE ON DATABASE workshop FROM catalog, fulfillment;\n'
    items=[secret('workshop-secrets',APP,data),secret('workshop-secrets',OBS,gateway),secret('postgres-init',APP,{'010-roles.sql':init})]
    path=GENERATED/'secrets.yaml';private_write(path,json.dumps({'apiVersion':'v1','kind':'List','items':items},indent=2));apply_file(path)
    collector_env={**gateway,'NODE_NAME':'validation-node'}
    private_write(GENERATED/'collector.env',''.join(k+'='+v+'\n' for k,v in collector_env.items()))

def collector_config():
    require(['WORKSHOP_SESSION_ID','WORKSHOP_ENVIRONMENT'])
    for name,filename in [('otel-agent','agent.yaml'),('otel-gateway','gateway-'+CFG['SENTRY_EXPORTER_MODE']+'.yaml')]:
        content=(ROOT/'infra/collectors'/filename).read_text()
        obj={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name+'-config','namespace':OBS},'data':{'config.yaml':content}}
        apply_obj(obj)

def validate_config():
    if not (GENERATED/'collector.env').exists():raise SystemExit('Run make configure before validating collector configs.')
    image=next(o for o in json.loads((ROOT/'infra/kubernetes/collectors.json').read_text())['items'] if o['kind']=='DaemonSet')['spec']['template']['spec']['containers'][0]['image']
    for name in ['agent.yaml','gateway-'+CFG['SENTRY_EXPORTER_MODE']+'.yaml']:
        run(['docker','run','--rm','--tmpfs','/var/lib/otelcol:mode=777','--env-file',GENERATED/'collector.env','-v',str(ROOT/'infra/collectors')+':/config:ro',image,'validate','--config=/config/'+name])
        print(name+': valid')

def configure():
    apply_file(ROOT/'infra/kubernetes/namespaces-rbac.json');secrets();collector_config();validate_config()

def build():
    for name,directory in COMPONENTS.items():
        dockerfile=ROOT/directory/'Dockerfile'
        if not dockerfile.exists():raise SystemExit('Missing '+str(dockerfile))
        run(['docker','build','--build-arg','SENTRY_RELEASE='+CFG['SENTRY_RELEASE'],'-t','workshop/'+name+':local','-f',dockerfile,'.'])
    run([BIN/'kind','load','docker-image',*['workshop/'+name+':local' for name in COMPONENTS],'--name',CLUSTER])

def gateway_install():
    if not CHART.exists():
        result=run([BIN/'helm','pull','oci://docker.io/envoyproxy/gateway-helm','--version','v1.9.2','--destination',CHART.parent],capture=True)
        if CHART_DIGEST not in result.stderr+result.stdout:raise SystemExit('Envoy chart OCI digest differed from the recorded release.')
    if hashlib.sha256(CHART.read_bytes()).hexdigest()!=CHART_SHA256:raise SystemExit('Envoy chart archive checksum mismatch.')
    run([BIN/'helm','upgrade','--install','eg',CHART,'--namespace','envoy-gateway-system','--kubeconfig',KUBECONFIG,'--kube-context',CONTEXT,'--values',ROOT/'infra/envoy-values.json','--wait','--timeout','180s'])
    apply_file(ROOT/'infra/kubernetes/gateway.json')

def migrations():
    kc('-n',APP,'exec','-i','postgres-0','--','psql','-U','workshop','-d','workshop','-v','ON_ERROR_STOP=1',input='CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION catalog; CREATE SCHEMA IF NOT EXISTS fulfillment AUTHORIZATION fulfillment; REVOKE CREATE ON DATABASE workshop FROM catalog, fulfillment;')
    for service_name,user in [('catalog','catalog'),('fulfillment','fulfillment')]:
        sql=ROOT/'services'/service_name/'migrations.sql'
        if not sql.exists():raise SystemExit('Missing migrations for '+service_name)
        # A temporary Job gets only its service database URL. Schema ownership stays separate.
        cm_name=service_name+'-migrations'
        apply_obj({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':cm_name,'namespace':APP},'data':{'migrations.sql':sql.read_text()}})
        job_name=service_name+'-migrate'
        kc('-n',APP,'delete','job',job_name,'--ignore-not-found=true')
        image=next(x for x in json.loads((ROOT/'infra/kubernetes/storage.json').read_text())['items'] if x['kind']=='StatefulSet' and x['metadata']['name']=='postgres')['spec']['template']['spec']['containers'][0]['image']
        obj={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':job_name,'namespace':APP},'spec':{'backoffLimit':0,'ttlSecondsAfterFinished':600,'template':{'metadata':{'labels':{'app.kubernetes.io/name':'migrations'}},'spec':{'automountServiceAccountToken':False,'restartPolicy':'Never','containers':[{'name':'psql','image':image,'env':[{'name':'DATABASE_URL','valueFrom':{'secretKeyRef':{'name':'workshop-secrets','key':service_name.upper()+'_DATABASE_URL'}}}],'command':['sh','-c','exec psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /migrations/migrations.sql'],'volumeMounts':[{'name':'sql','mountPath':'/migrations','readOnly':True}],'resources':{'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'memory':'128Mi'}}}],'volumes':[{'name':'sql','configMap':{'name':cm_name}}]}}}}
        apply_obj(obj)
        kc('-n',APP,'wait','--for=condition=complete','job/'+job_name,'--timeout=90s')

def forward():
    state=LOCAL/'port-forward.json'
    if state.exists():
        old=json.loads(state.read_text())
        try:
            cmd=run(['ps','-p',str(old['pid']),'-o','command='],capture=True).stdout
            if str(KUBECONFIG) in cmd and 'port-forward' in cmd and '8080:80' in cmd:
                print('Gateway already forwarded at http://localhost:8080');return
        except subprocess.CalledProcessError:pass
        state.unlink()
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',8080))==0:raise SystemExit('Port8080 already belongs to another process; refusing to replace it.')
    svc=json.loads(kc('-n',APP,'get','service','-l','gateway.envoyproxy.io/owning-gateway-name=workshop','-o','json',capture=True).stdout)['items']
    if len(svc)!=1:raise SystemExit('Expected exactly one workshop Envoy proxy service.')
    name=svc[0]['metadata']['name']
    output=open(LOCAL/'port-forward.log','a')
    process=subprocess.Popen([str(BIN/'kubectl'),'--kubeconfig',str(KUBECONFIG),'--context',CONTEXT,'-n',APP,'port-forward','service/'+name,'8080:80','--address','127.0.0.1'],cwd=ROOT,env=ENV,stdout=output,stderr=output,start_new_session=True)
    output.close();private_write(state,json.dumps({'pid':process.pid,'service':name,'url':'http://localhost:8080'}))
    for _ in range(50):
        if process.poll() is not None:raise SystemExit('Port-forward failed; see .local/port-forward.log.')
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',8080))==0:print('Gateway: http://localhost:8080');return
        time.sleep(.2)
    raise SystemExit('Timed out waiting for gateway forwarding.')

def forward_stop():
    state=LOCAL/'port-forward.json'
    if not state.exists():return
    old=json.loads(state.read_text())
    result=run(['ps','-p',str(old['pid']),'-o','command='],capture=True,check=False)
    if str(KUBECONFIG) in result.stdout and 'port-forward' in result.stdout and '8080:80' in result.stdout:
        os.kill(old['pid'],signal.SIGTERM)
    state.unlink()

def deploy():
    configure();apply_file(ROOT/'infra/kubernetes/storage.json')
    for n in ['postgres','redis']:kc('-n',APP,'rollout','status','statefulset/'+n,'--timeout=120s')
    migrations();gateway_install();kc('apply','-k',ROOT/'infra/kubernetes')
    # ConfigMap/Secret updates require a restart; gateway Recreate preserves exclusive queues during exporter mode switches.
    kc('-n',OBS,'rollout','restart','daemonset/otel-agent','deployment/otel-gateway')
    for n in ['otel-agent','otel-gateway']:kc('-n',OBS,'rollout','status',('daemonset/' if n=='otel-agent' else 'deployment/')+n,'--timeout=120s')
    for n in COMPONENTS:kc('-n',APP,'rollout','restart','deployment/'+n);kc('-n',APP,'rollout','status','deployment/'+n,'--timeout=180s')
    kc('-n',APP,'wait','--for=condition=Programmed','gateway/workshop','--timeout=120s');forward()

def network_verify():
    image='busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e'
    for allowed in [True,False]:
        name='workshop-net-'+('allowed' if allowed else 'denied')+'-'+str(int(time.time()))
        label='catalog' if allowed else 'network-probe'
        args=['sh','-c','nc -z -w 3 postgres 5432']
        obj={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':APP,'labels':{'app.kubernetes.io/name':label}},'spec':{'automountServiceAccountToken':False,'restartPolicy':'Never','containers':[{'name':'probe','image':image,'command':args,'resources':{'requests':{'cpu':'10m','memory':'16Mi'},'limits':{'memory':'32Mi'}}}]}}
        try:
            apply_obj(obj);phase=''
            for _ in range(60):
                phase=json.loads(kc('-n',APP,'get','pod',name,'-o','json',capture=True).stdout)['status']['phase']
                if phase in ['Succeeded','Failed']:break
                time.sleep(1)
            expected='Succeeded' if allowed else 'Failed'
            if phase!=expected:raise SystemExit(f'NetworkPolicy {label} probe expected {expected}, got {phase}.')
            print('NetworkPolicy '+('allowed' if allowed else 'denied')+' probe passed')
        finally:kc('-n',APP,'delete','pod',name,'--ignore-not-found=true','--wait=false')
    # Use a real web Pod so the app-specific egress selector is exercised.
    denied=kc('-n',APP,'exec','deployment/web','--','node','-e',"fetch('http://example.com',{signal:AbortSignal.timeout(3000)}).then(()=>{console.error('Unexpected port80 egress');process.exit(1)}).catch(e=>{if(e.name==='TimeoutError'||e.name==='AbortError'){console.log('Public port80 egress denied')}else{console.error(e.name);process.exit(1)}})",capture=True)
    print(denied.stdout.strip())
    allowed=kc('-n',APP,'exec','deployment/web','--','node','-e',"fetch(new URL(process.env.SENTRY_DSN).origin,{signal:AbortSignal.timeout(10000)}).then(r=>console.log('Sentry HTTPS egress allowed; HTTP'+r.status)).catch(e=>{console.error(e.name);process.exit(1)})",capture=True)
    print(allowed.stdout.strip())

def verify():
    forward()
    with urlopen('http://localhost:8080/health/ready',timeout=10) as response:
        if response.status!=200:raise SystemExit('Web is not ready through Envoy.')
    network_verify()
    workflow_script=ROOT/'scripts/verify-workflows.py'
    if workflow_script.exists():run([sys.executable,workflow_script])
    print('Local routing, readiness, and NetworkPolicy verification passed.')
    private_write(LOCAL/'verification/infra.json',json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'context':CONTEXT,'checks':['envoy-ready','network-allowed','network-denied']},indent=2))

def down():
    forward_stop()
    print('Workshop browser forwarding stopped; cluster workloads and PVCs retained.')

def cluster_delete():
    guarded();forward_stop();run([BIN/'kind','delete','cluster','--name',CLUSTER]);KUBECONFIG.unlink(missing_ok=True)
    (LOCAL/'cluster.json').unlink(missing_ok=True);print('Deleted only the named workshop cluster, including its demo volumes.')

def reset_demo():
    for name in COMPONENTS:kc('-n',APP,'scale','deployment/'+name,'--replicas=0')
    for name in COMPONENTS:
        kc('-n',APP,'wait','--for=delete','pod','-l','app.kubernetes.io/name='+name,'--timeout=90s')
    sql='TRUNCATE fulfillment.outbox, fulfillment.orders, fulfillment.runs, catalog.products; UPDATE fulfillment.inventory SET available_quantity=10000 WHERE product_id=\'demo-widget\';'
    kc('-n',APP,'exec','-i','postgres-0','--','psql','-U','workshop','-d','workshop','-v','ON_ERROR_STOP=1',input=sql)
    kc('-n',APP,'exec','redis-0','--','sh','-c','export REDISCLI_AUTH="$REDIS_PASSWORD"; redis-cli --scan --pattern "workshop:*" | while IFS= read -r key; do redis-cli DEL "$key" >/dev/null; done')
    migrations()
    session='workshop-'+uuid.uuid4().hex[:12]
    path=ROOT/'.env';lines=path.read_text().splitlines();updated=False
    for i,line in enumerate(lines):
        if line.startswith('WORKSHOP_SESSION_ID='):lines[i]='WORKSHOP_SESSION_ID='+session;updated=True
    if not updated:lines.append('WORKSHOP_SESSION_ID='+session)
    private_write(path,'\n'.join(lines)+'\n')
    CFG['WORKSHOP_SESSION_ID']=session;ENV['WORKSHOP_SESSION_ID']=session
    secrets();collector_config()
    kc('-n',OBS,'rollout','restart','daemonset/otel-agent','deployment/otel-gateway')
    for name in ['otel-agent','otel-gateway']:kc('-n',OBS,'rollout','status',('daemonset/' if name=='otel-agent' else 'deployment/')+name,'--timeout=120s')
    for name in COMPONENTS:
        kc('-n',APP,'scale','deployment/'+name,'--replicas=1')
        kc('-n',APP,'rollout','status','deployment/'+name,'--timeout=120s')
    print('Synthetic workshop rows and Redis workshop:* keys reset; new session '+session)

def up():
    import bootstrap
    bootstrap.install();cluster_up();build();deploy();verify()

def main():
    commands={'doctor':doctor,'cluster-up':cluster_up,'configure':configure,'validate-config':validate_config,'build':build,'deploy':deploy,'forward':forward,'forward-stop':forward_stop,'verify':verify,'verify-network':network_verify,'down':down,'cluster-delete':cluster_delete,'reset-demo':reset_demo,'up':up,'migrate':migrations}
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=commands);args=parser.parse_args()
    LOCAL.mkdir(exist_ok=True);GENERATED.mkdir(exist_ok=True)
    commands[args.command]()
if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError as exc:
        print('Command failed with exit code '+str(exc.returncode),file=sys.stderr)
        if exc.stderr:print(exc.stderr,file=sys.stderr)
        raise SystemExit(exc.returncode)
