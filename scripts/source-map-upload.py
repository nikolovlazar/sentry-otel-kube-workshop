#!/usr/bin/env python3
"""Upload already injected source maps extracted from the exact local runtime images."""
import json
import re
from pathlib import Path
import shutil
import sys
import uuid
import workshop as w

COMPONENTS = [('web','/app/apps/web/.next','sentry-otel-workshop-web'),('notification-worker','/app/dist','sentry-otel-workshop-worker')]
RECOVERY = 'Run make build && make deploy, wait for both rollouts, then rerun make source-map-upload.'

def fail_guard(component, reason):
    raise SystemExit(f'Source-map safety check failed for {component}: {reason}. {RECOVERY}')

def digest(reference):
    match = re.search(r'sha256:[0-9a-f]{64}$', reference or '')
    return match.group(0) if match else None

def verify_deployed_images(expected_images=None):
    """Compare immutable content identities and actual process release metadata before upload."""
    nodes = set(w.run([w.BIN/'kind','get','nodes','--name',w.CLUSTER],capture=True).stdout.splitlines())
    images = {}
    for component, _, _ in COMPONENTS:
        image = 'workshop/'+component+':local'
        result = w.run(['docker','--context',w.CFG['DOCKER_CONTEXT'],'image','inspect',image,'--format','{{.Id}}'],capture=True,check=False)
        local_id = digest(result.stdout.strip())
        if result.returncode or not local_id:
            fail_guard(component,'selected local Docker image is unavailable')
        if expected_images is not None and local_id != expected_images[component]:
            fail_guard(component,'selected local Docker image changed during extraction')
        images[component] = local_id
        result = w.kc('-n',w.APP,'get','pods','-l','app.kubernetes.io/name='+component,'-o','json',capture=True,check=False)
        if result.returncode:
            fail_guard(component,'could not inspect deployed containers')
        pods = json.loads(result.stdout).get('items',[])
        running = [(pod,status) for pod in pods for status in pod.get('status',{}).get('containerStatuses',[]) if status.get('name') == component and status.get('state',{}).get('running') is not None]
        if not running:
            fail_guard(component,'no running container is available')
        for pod, status in running:
            pod_name = pod['metadata']['name']
            node = pod.get('spec',{}).get('nodeName')
            if node not in nodes:
                fail_guard(component,'running container is outside the named workshop kind cluster')
            actual_id = digest(status.get('imageID'))
            if actual_id != local_id:
                # kind/containerd can expose a manifest reference instead of Docker's config ID.
                # Resolve the selected tag, then verify BOTH the Pod reference and the config ID.
                result = w.run(['docker','--context',w.CFG['DOCKER_CONTEXT'],'exec',node,'crictl','inspecti',image],capture=True,check=False)
                if result.returncode:
                    fail_guard(component,'could not resolve the deployed runtime image')
                runtime = json.loads(result.stdout).get('status',{})
                references = {digest(runtime.get('id')), *[digest(ref) for ref in runtime.get('repoDigests',[])]}
                if not actual_id or actual_id not in references or digest(runtime.get('id')) != local_id:
                    fail_guard(component,'running container image differs from the selected local Docker image')
            result = w.kc('-n',w.APP,'exec',pod_name,'-c',component,'--','node','-e',"process.stdout.write(JSON.stringify({SENTRY_RELEASE:process.env.SENTRY_RELEASE,SERVICE_VERSION:process.env.SERVICE_VERSION}))",capture=True,check=False)
            if result.returncode:
                fail_guard(component,'could not read the running process release metadata')
            runtime = json.loads(result.stdout)
            for key in ['SENTRY_RELEASE','SERVICE_VERSION']:
                if not w.CFG.get(key) or runtime.get(key) != w.CFG[key]:
                    fail_guard(component,f'runtime {key} does not match the shared configuration')
    return images

def main():
    w.require(['SENTRY_SOURCE_MAP_AUTH_TOKEN','SENTRY_ORG_SLUG','SENTRY_RELEASE','SERVICE_VERSION'])
    selected_images=verify_deployed_images()
    for component,path,project in COMPONENTS:
        verify_deployed_images(selected_images)
        target=w.LOCAL/'source-maps'/component
        if target.exists():shutil.rmtree(target)
        target.mkdir(parents=True)
        cid=w.run(['docker','--context',w.CFG['DOCKER_CONTEXT'],'create','--name','workshop-sourcemaps-'+uuid.uuid4().hex[:8],selected_images[component]],capture=True).stdout.strip()
        try:
            sources=['static','server'] if component=='web' else ['']
            for subtree in sources:
                destination=target/subtree
                destination.mkdir(exist_ok=True)
                w.run(['docker','--context',w.CFG['DOCKER_CONTEXT'],'cp',cid+':'+path+('/'+subtree if subtree else '')+'/.',destination])
        finally:w.run(['docker','--context',w.CFG['DOCKER_CONTEXT'],'rm',cid],capture=True)
        maps=list(target.rglob('*.map'))
        if not maps:raise SystemExit('No source maps in running-image assets for '+component)
        injected=[p for p in maps if json.loads(p.read_text()).get('debug_id') or json.loads(p.read_text()).get('debugId')]
        if not injected:raise SystemExit('Image source maps lack debug IDs for '+component+'; fix its build before upload.')
        env={**w.ENV,'SENTRY_AUTH_TOKEN':w.CFG['SENTRY_SOURCE_MAP_AUTH_TOKEN'],'SENTRY_ORG':w.CFG['SENTRY_ORG_SLUG'],'SENTRY_PROJECT':project}
        env.pop('SENTRY_DSN',None)
        cli=w.ROOT/'services/notification-worker/node_modules/.bin/sentry-cli'
        if not cli.exists():
            import subprocess
            subprocess.run([str(w.BIN/'npm'),'ci'],cwd=w.ROOT/'services/notification-worker',env=w.ENV,check=True)
        env['SENTRY_URL']=w.CFG['SENTRY_BASE_URL']
        verify_deployed_images(selected_images)
        result=w.run([cli,'sourcemaps','upload',target,'--release',w.CFG['SENTRY_RELEASE'],'--no-rewrite','--wait-for','120'],env=env,capture=True,check=False)
        details=result.stdout+result.stderr
        w.private_write(w.LOCAL/('verification/source-maps-'+component+'.log'),details)
        if result.returncode or 'processing complete' not in details.lower():
            raise SystemExit('Source-map upload/processing did not complete for '+component+'; see its private .local/verification/source-maps log.')
        print(f'{component}: server processed {len(maps)} maps from the runtime image, without modifying deployed bytes')
if __name__=='__main__':main()
