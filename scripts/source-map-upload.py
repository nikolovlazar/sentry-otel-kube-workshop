#!/usr/bin/env python3
"""Upload already injected source maps extracted from the exact local runtime images."""
import json
from pathlib import Path
import shutil
import sys
import uuid
import workshop as w

def main():
    w.require(['SENTRY_SOURCE_MAP_AUTH_TOKEN','SENTRY_ORG_SLUG'])
    for component,path,project in [('web','/app/apps/web/.next','sentry-otel-workshop-web'),('notification-worker','/app/dist','sentry-otel-workshop-worker')]:
        target=w.LOCAL/'source-maps'/component
        if target.exists():shutil.rmtree(target)
        target.mkdir(parents=True)
        cid=w.run(['docker','create','--name','workshop-sourcemaps-'+uuid.uuid4().hex[:8],'workshop/'+component+':local'],capture=True).stdout.strip()
        try:
            sources=['static','server'] if component=='web' else ['']
            for subtree in sources:
                destination=target/subtree
                destination.mkdir(exist_ok=True)
                w.run(['docker','cp',cid+':'+path+('/'+subtree if subtree else '')+'/.',destination])
        finally:w.run(['docker','rm',cid],capture=True)
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
        result=w.run([cli,'sourcemaps','upload',target,'--release',w.CFG['SENTRY_RELEASE'],'--no-rewrite'],env=env,capture=True)
        w.private_write(w.LOCAL/('verification/source-maps-'+component+'.log'),result.stdout+result.stderr)
        print(f'{component}: uploaded {len(maps)} maps from the runtime image, without modifying deployed bytes')
if __name__=='__main__':main()
