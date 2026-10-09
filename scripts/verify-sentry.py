#!/usr/bin/env python3
"""Require live OTLP spans, stdout logs, readable SDK errors and shared span context."""
from datetime import datetime, timezone
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import workshop as w

PREFIX = 'sentry-otel-workshop-'
SCENARIOS = [
    ('full-journey', 'success'), ('cache-miss-hit', 'success'),
    ('slow-postgres', 'success'), ('python-error', 'success'),
    ('go-error', 'success'), ('next-error', 'success'),
    ('async-worker', 'recover'), ('async-worker', 'exhaust'),
]
SERVICES = {'web': 'web', 'catalog': 'catalog', 'fulfillment': 'fulfillment', 'notification-worker': 'worker'}


def local(path, payload=None):
    request = Request('http://localhost:8080' + path, data=json.dumps(payload).encode() if payload is not None else None, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response: return json.load(response)
    except HTTPError as error:
        value = json.load(error)
        if 'runId' not in value: raise
        return value


def api(path, params=None):
    url = w.CFG['SENTRY_BASE_URL'].rstrip('/') + '/api/0/' + path.lstrip('/')
    if params: url += '?' + urlencode(params, doseq=True)
    request = Request(url, headers={'Authorization': 'Bearer ' + w.CFG['SENTRY_AUTH_TOKEN'], 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response: return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'Sentry API returned HTTP{error.code} for {path}') from None


def required_services(scenario):
    if scenario == 'next-error': return {'web', 'fulfillment'}
    if scenario in ['python-error', 'go-error']: return {'web', 'catalog', 'fulfillment'}
    return set(SERVICES)


def readable_frames(event):
    frames = []
    for entry in event.get('entries', []):
        if entry.get('type') == 'exception':
            for value in entry.get('data', {}).get('values', []):
                frames.extend((value.get('stacktrace') or {}).get('frames', []))
    return [frame for frame in frames if re.search(r'\.(py|go|tsx?)$', frame.get('filename') or frame.get('absPath') or '') and frame.get('function') and frame.get('lineNo')]


def parentage_problem(spans, scenario):
    indexed = {span["span_id"]: span for span in spans}
    for span in spans:
        name = span.get("span.name") or ""
        parent = indexed.get(span.get("parent_span"))
        if span["project"] == PREFIX + "catalog" and name == "GET /v1/products/demo-widget":
            if not parent or parent["project"] != PREFIX + "web" or parent.get("span.name") != "catalog.get_product":
                return "catalog HTTP server does not descend from the web HTTP client"
        if span["project"] == PREFIX + "fulfillment" and name.startswith("workshop.v1.Fulfillment/"):
            if not parent or parent["project"] not in {PREFIX + "web", PREFIX + "worker"} or parent.get("span.name") != name:
                return "Go RPC server does not descend from its matching client"
        if span["project"] == PREFIX + "worker" and name == "notification.process":
            if not parent or parent["project"] != PREFIX + "fulfillment" or parent.get("span.name") != "notifications publish":
                return "worker consumer does not descend from the persisted producer"
    consumers = [span for span in spans if span.get("span.name") == "notification.process"]
    if scenario == "async-worker" and (len(consumers) != 3 or len({span.get("parent_span") for span in consumers}) != 1):
        return "three retry consumers are not siblings under one producer"
    if scenario == "slow-postgres" and not any(span["project"] == PREFIX + "catalog" and span.get("span.op") == "db" and (span.get("span.duration") or 0) >= 1200 for span in spans):
        return "slow query is not explained by a database span of at least1200ms"
    return ""


def main():
    w.require(['SENTRY_AUTH_TOKEN', 'SENTRY_ORG_SLUG', 'SENTRY_BASE_URL'])
    w.forward()
    for suffix in [*SERVICES.values(), 'infrastructure']:
        api('projects/' + w.CFG['SENTRY_ORG_SLUG'] + '/' + PREFIX + suffix + '/')
    evidence = {'timestamp': datetime.now(timezone.utc).isoformat(), 'sessionId': w.CFG['WORKSHOP_SESSION_ID'], 'exporterMode': w.CFG['SENTRY_EXPORTER_MODE'], 'runs': []}
    # Start the bounded matrix first so the normal ingestion delay is shared across cases.
    started = [(scenario, mode, local('/api/runs', {'scenario': scenario, 'workerMode': mode, 'sessionId': w.CFG['WORKSHOP_SESSION_ID']})) for scenario, mode in SCENARIOS]
    for scenario, mode, run in started:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            final = run['status'] in ['completed', 'failed', 'dead_lettered']
            if final and (run['status'] == 'completed' or run.get('errorEventId')): break
            time.sleep(.5); run = local('/api/runs/' + run['runId'])
        if run['status'] not in ['completed', 'failed', 'dead_lettered']: raise SystemExit('Workflow did not terminate: ' + run['runId'])
        trace = run['traceId']
        if not re.fullmatch('[a-f0-9]{32}', trace) or trace == '0' * 32: raise SystemExit('Workflow has no valid traceId: ' + run['runId'])
        expected = 'dead_lettered' if mode == 'exhaust' else ('failed' if scenario.endswith('error') else 'completed')
        if run['status'] != expected: raise SystemExit('Unexpected terminal state for ' + scenario + '/' + mode)
        required = required_services(scenario)
        required_projects = {PREFIX + SERVICES[service] for service in required} | {PREFIX + 'infrastructure'}
        required_log_projects = required_projects - {PREFIX + 'infrastructure'}
        deadline = time.monotonic() + 120
        org = 'organizations/' + w.CFG['SENTRY_ORG_SLUG'] + '/events/'
        params = {'statsPeriod': '1h', 'query': 'trace:' + trace, 'per_page': 100}
        spans = []; logs = []; event = None; reason = ''
        while time.monotonic() < deadline:
            spans = api(org, {**params, 'dataset': 'spans', 'field': ['trace', 'span_id', 'parent_span', 'span.name', 'span.description', 'span.duration', 'span.op', 'project', 'resource.service.name', 'resource.service.namespace', 'resource.service.version', 'resource.k8s.pod.uid', 'resource.k8s.pod.name', 'resource.k8s.namespace.name', 'resource.k8s.node.name', 'resource.k8s.cluster.name']}).get('data', [])
            logs = api(org, {**params, 'dataset': 'logs', 'field': ['trace', 'span_id', 'message', 'project', 'service.name', 'log.record_id', 'resource.service.namespace', 'resource.service.version', 'resource.k8s.pod.uid', 'resource.k8s.pod.name', 'resource.k8s.namespace.name', 'resource.k8s.node.name', 'resource.k8s.cluster.name']}).get('data', [])
            span_ids = {span['span_id'] for span in spans}
            missing_spans = required_projects - {span['project'] for span in spans}
            missing_logs = required_log_projects - {log['project'] for log in logs}
            if missing_spans or missing_logs:
                reason = 'missing span/log projects: ' + ','.join(sorted(missing_spans | missing_logs))
            elif parentage_problem(spans, scenario):
                reason = parentage_problem(spans, scenario)
            elif any(log.get('span_id') not in span_ids for log in logs):
                reason = 'stdout log span_id is absent from exported spans'
            elif any(not row.get('resource.k8s.pod.uid') or not row.get('resource.k8s.pod.name') or row.get('resource.k8s.namespace.name') != w.APP or row.get('resource.k8s.cluster.name') != w.CLUSTER or row.get('resource.service.namespace') != w.APP for row in [*spans, *logs] if row['project'] != PREFIX + 'infrastructure'):
                reason = 'custom workload is missing correct Kubernetes resource metadata'
            elif any(row.get('resource.service.version') != w.CFG['SERVICE_VERSION'] for row in [*spans, *logs] if row['project'] != PREFIX + 'infrastructure'):
                reason = 'custom workload service.version differs from the deployed Git revision'
            elif any(not log.get('log.record_id') for log in logs if log['project'] != PREFIX + 'infrastructure'):
                reason = 'stdout log has no unique log.record_id'
            elif len([log['log.record_id'] for log in logs if log['project'] != PREFIX + 'infrastructure']) != len({log['log.record_id'] for log in logs if log['project'] != PREFIX + 'infrastructure'}):
                reason = 'duplicate stdout log.record_id within the trace'
            elif run['status'] == 'completed':
                errors = api(org, {**params, 'dataset': 'errors', 'field': ['id', 'project', 'trace']}).get('data', [])
                if errors: reason = 'successful workflow unexpectedly has an SDK error'
                else: break
            else:
                event_id = run.get('errorEventId', '')
                if not re.fullmatch('[a-f0-9]{32}', event_id): raise SystemExit('Failure has no SDK event ID: ' + run['runId'])
                suffix = SERVICES.get(run.get('failureService'))
                if not suffix: raise SystemExit('Unknown failureService: ' + run['runId'])
                try:
                    event = api('projects/' + w.CFG['SENTRY_ORG_SLUG'] + '/' + PREFIX + suffix + '/events/' + event_id + '/')
                    context = event.get('contexts', {}).get('trace', {})
                    if context.get('trace_id') != trace: raise SystemExit('SDK error trace_id differs from OTLP trace: ' + run['runId'])
                    if context.get('span_id') not in span_ids: reason = 'SDK error active span_id is absent from exported spans'
                    elif not readable_frames(event): reason = 'SDK error has no readable original source frame'
                    else:
                        errors = api(org, {**params, 'dataset': 'errors', 'field': ['id', 'project', 'trace']}).get('data', [])
                        if len(errors) != 1 or errors[0].get('id') != event_id:
                            reason = 'workflow lacks exactly one originating SDK error'
                        else: break
                except RuntimeError as error: reason = str(error)
            time.sleep(3)
        else: raise SystemExit('Sentry verification timed out for ' + run['runId'] + ': ' + reason)
        frames = readable_frames(event) if event else []
        evidence['runs'].append({**run, 'spanCount': len(spans), 'logCount': len(logs), 'spans': spans, 'logs': logs, 'errorTraceContext': event.get('contexts', {}).get('trace') if event else None, 'readableSourceFrames': frames})
        w.private_write(w.LOCAL / 'verification/sentry.json', json.dumps(evidence, indent=2))
        print(f'{scenario}/{mode}: {run["status"]}, trace {trace}, {len(spans)} spans/{len(logs)} logs' + (', readable error ' + run['errorEventId'] if event else ''), flush=True)
    print('Eight live Sentry permutations passed; browser errors are verified separately in the UI. Evidence: .local/verification/sentry.json')


if __name__ == '__main__': main()
