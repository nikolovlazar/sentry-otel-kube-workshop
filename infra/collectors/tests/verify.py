#!/usr/bin/env python3
"""Exercise the pinned Collector against native container-log fixtures, without Sentry credentials."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
IMAGE = 'otel/opentelemetry-collector-contrib:0.162.0@sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6'
TRACE_ID = '1234567890abcdef1234567890abcdef'
SPAN_ID = '1234567890abcdef'


def run(*args: str, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-6000:])
    return result


def attributes(record):
    return {a['key']: next(iter(a['value'].values())) for a in record.get('attributes', [])}


def records(path):
    parsed = []
    if path.exists():
        for line in path.read_text().splitlines():
            batch = json.loads(line)
            for resource in batch.get('resourceLogs', []):
                resource_attrs = attributes(resource.get('resource', {}))
                for scope in resource.get('scopeLogs', []):
                    for record in scope.get('logRecords', []):
                        parsed.append((resource_attrs, record))
    return parsed


def fixtures(base):
    lines = {'catalog': [], 'postgres': [], 'redis': []}
    def add(service, body):
        lines[service].append('2026-10-13T16:00:00.000000000Z stderr F ' + body + '\n')
    def pg(message, severity='LOG', sqlstate='00000', application_name='fixture'):
        add('postgres', f'2026-10-13 16:00:00.001 UTC|123|{application_name}|{sqlstate}|{severity}:  {message}')
    for i in range(2000):
        pg(f'fixture-routine-pg-{i}')
        add('redis', f'1:M 13 Oct 2026 16:00:00.002 * fixture-routine-redis-{i}')
    for i in range(20):
        add('catalog', json.dumps({'body': f'fixture-app-{i}', 'severity': 'INFO', 'log.record_id': f'app-record-{i}', 'trace_id': TRACE_ID, 'span_id': SPAN_ID, 'trace_flags': 1}))
    pg('fixture-pg-warning', 'WARNING')
    pg('fixture-pg-error', 'ERROR', '55P03', f'w:{TRACE_ID}:{SPAN_ID}')
    pg('fixture-pg-statement', 'STATEMENT', '55P03', f'w:{TRACE_ID}:{SPAN_ID}')
    pg('duration: 1203.000 ms  statement: SELECT pg_sleep(1.2) /* fixture-slow */', application_name=f'w:{TRACE_ID}:{SPAN_ID}')
    pg('process 123 still waiting for AccessShareLock on relation 456 after 250.123 ms /* fixture-wait */')
    pg('fixture-invalid-context', 'WARNING', application_name='w:not-a-trace:not-a-span')
    pg('fixture-zero-context', 'WARNING', application_name='w:' + '0' * 32 + ':' + '0' * 16)
    pg('fixture-normal-context', 'WARNING', application_name='health')
    add('redis', '1:M 13 Oct 2026 16:00:00.003 # fixture-redis-warning')
    for service, entries in lines.items():
        pod_id = str(uuid.uuid4())
        directory = base / f'sentry-otel-workshop_{service}-fixture_{pod_id}' / service
        directory.mkdir(parents=True)
        (directory / '0.log').write_text(''.join(entries))


def case(mode, rate):
    agent = json.loads((ROOT / 'infra/collectors/agent.json').read_text())
    gateway = json.loads((ROOT / f'infra/collectors/gateway-{mode}.json').read_text())
    config = {'receivers': {'filelog': copy.deepcopy(agent['receivers']['filelog'])}, 'processors': {}, 'exporters': {'file': {'path': '/out/logs.jsonl', 'flush_interval': '1s'}}, 'service': {'pipelines': {'logs': {'receivers': ['filelog'], 'processors': [], 'exporters': ['file']}}, 'telemetry': {'logs': {'level': 'warn'}}}}
    config['receivers']['filelog'].pop('storage')
    config['receivers']['filelog']['start_at'] = 'beginning'
    # Only Kubernetes API enrichment is replaced. The real container-path operator and
    # native parsers are exercised; fixture labels emulate the known pod service labels.
    config['processors']['transform/fixture-resources'] = {'error_mode': 'propagate', 'log_statements': [{'context': 'resource', 'statements': ['set(attributes["service.name"], attributes["k8s.container.name"])']} ]}
    processor_order = ['transform/fixture-resources', 'resource', 'transform/names', 'transform/infra-logs', 'attributes/redact', 'transform/log-priority', 'probabilistic_sampler/infra-logs', 'batch']
    for name in processor_order:
        if name != 'transform/fixture-resources':
            config['processors'][name] = copy.deepcopy(agent['processors'].get(name, gateway['processors'].get(name)))
    config['processors']['resource']['attributes'] = [{'key': 'workshop.fixture', 'value': True, 'action': 'insert'}]
    config['processors']['probabilistic_sampler/infra-logs']['sampling_percentage'] = rate
    config['service']['pipelines']['logs']['processors'] = processor_order
    with tempfile.TemporaryDirectory(prefix='workshop-collector-fixtures-') as temp:
        tmp = Path(temp)
        log_directory = tmp / 'pods'
        log_directory.mkdir()
        fixtures(log_directory)
        out = tmp / 'out'
        out.mkdir()
        (tmp / 'config.json').write_text(json.dumps(config))
        name = 'workshop-fixture-' + uuid.uuid4().hex[:12]
        try:
            run('docker', 'run', '-d', '--network=none', '--user=0', '--name', name, '-v', f'{tmp / "config.json"}:/config.json:ro', '-v', f'{log_directory}:/var/log/pods:ro', '-v', f'{out}:/out', IMAGE, '--config=/config.json')
            deadline = time.monotonic() + 15
            previous_size = -1
            stable = 0
            while time.monotonic() < deadline:
                time.sleep(0.5)
                state = run('docker', 'inspect', '--format', '{{.State.Running}}', name).stdout.strip()
                if state != 'true':
                    raise AssertionError('Collector failed: ' + run('docker', 'logs', name).stderr[-6000:])
                size = (out / 'logs.jsonl').stat().st_size if (out / 'logs.jsonl').exists() else 0
                stable = stable + 1 if size > 0 and size == previous_size else 0
                previous_size = size
                if stable >= 4:
                    break
            run('docker', 'stop', '--time', '5', name)
            collector_logs = run('docker', 'logs', name)
            assert 'Error' not in collector_logs.stderr and 'failed to process' not in collector_logs.stderr, collector_logs.stderr[-6000:]
            data = records(out / 'logs.jsonl')
            by_body = {record['body']['stringValue']: (resource, record) for resource, record in data}
            assert len(data) == len(by_body), 'Repeated log records'
            app = [body for body in by_body if body.startswith('fixture-app-')]
            assert len(app) == 20, f'{mode}/{rate}: lost application records'
            kept = ['fixture-pg-warning', 'fixture-pg-error', 'fixture-pg-statement', 'fixture-invalid-context', 'fixture-zero-context', 'fixture-normal-context', 'fixture-redis-warning', 'duration: 1203.000 ms  statement: SELECT pg_sleep(1.2) /* fixture-slow */', 'process 123 still waiting for AccessShareLock on relation 456 after 250.123 ms /* fixture-wait */']
            assert all(body in by_body for body in kept), f'Missing retained evidence: {[body for body in kept if body not in by_body]}'
            native_ids = []
            for resource, record in data:
                attrs = attributes(record)
                assert record.get('timeUnixNano'), 'Native timestamp missing'
                assert resource['k8s.namespace.name'] == 'sentry-otel-workshop'
                assert resource.get('k8s.pod.uid'), 'Container-path pod UID missing'
                if resource['service.name'] in ['postgres', 'redis']:
                    native_ids.append(attrs['log.record_id'])
                    assert attrs['log.source'] == resource['service.name']
                    assert attrs.get('process.pid')
                else:
                    assert attrs['log.record_id'].startswith('app-record-'), 'Application record ID overwritten'
            assert len(set(native_ids)) == len(native_ids), 'Infrastructure IDs not unique'
            for body in ['fixture-pg-error', 'fixture-pg-statement', kept[-2]]:
                _, record = by_body[body]
                assert record['traceId'] == TRACE_ID and record['spanId'] == SPAN_ID, (body, record)
            for body in ['fixture-invalid-context', 'fixture-zero-context', 'fixture-normal-context', 'fixture-redis-warning']:
                assert not by_body[body][1].get('traceId'), f'Invented trace context for {body}'
            assert by_body['fixture-pg-error'][1]['severityNumber'] == 17
            assert by_body['fixture-pg-statement'][1]['severityNumber'] == 9
            assert by_body['fixture-redis-warning'][1]['severityNumber'] == 13
            counts = {service: sum(body.startswith(f'fixture-routine-{service}-') for body in by_body) for service in ['pg', 'redis']}
            for service, count in counts.items():
                ratio = count / 2000
                assert ratio == 1 if rate == 100 else abs(ratio - rate / 100) < 0.035, (mode, rate, service, count)
            return {'mode': mode, 'rate': rate, 'routineGenerated': {'postgres': 2000, 'redis': 2000}, 'routineExported': counts, 'applicationGenerated': 20, 'applicationExported': len(app), 'incidentGenerated': len(kept), 'incidentExported': len(kept), 'nativeContextValidated': True, 'nativeTimestampSeverityPodIdentityValidated': True, 'uniqueInfrastructureRecordIds': len(native_ids)}
        finally:
            subprocess.run(['docker', 'rm', '-fv', name], capture_output=True)


def verify_storage():
    storage = json.loads((ROOT / 'infra/kubernetes/storage.json').read_text())
    postgres = next(item for item in storage['items'] if item['kind'] == 'StatefulSet' and item['metadata']['name'] == 'postgres')['spec']['template']['spec']['containers'][0]
    name = 'workshop-storage-fixture-' + uuid.uuid4().hex[:12]
    try:
        # No host port or network; trust is restricted to this disposable init fixture.
        run('docker', 'run', '-d', '--network=none', '--tmpfs', '/var/lib/postgresql/data', '--name', name, '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', postgres['image'], *postgres['args'])
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            output = run('docker', 'logs', name)
            logs = output.stdout + output.stderr
            if 'PostgreSQL init process complete; ready for start up.' in logs and logs.count('database system is ready to accept connections') >= 2:
                ready = True
                break
            if run('docker', 'inspect', '--format', '{{.State.Running}}', name).stdout.strip() != 'true':
                raise AssertionError('Fresh PostgreSQL initialization failed: ' + logs[-4000:])
            time.sleep(0.25)
        assert ready, 'Fresh PostgreSQL initialization did not finish'
        sql = f"SET application_name='w:{TRACE_ID}:{SPAN_ID}'; SELECT pg_sleep(0.55); DO $$ BEGIN RAISE LOG 'native-storage-fixture'; END $$;"
        run('docker', 'exec', name, 'psql', '-U', 'postgres', '-c', sql)
        output = run('docker', 'logs', name)
        logs = output.stdout + output.stderr
        duration = next(line for line in logs.splitlines() if 'duration:' in line)
        assert f'|w:{TRACE_ID}:{SPAN_ID}|00000|LOG:' in duration, duration
    finally:
        subprocess.run(['docker', 'rm', '-fv', name], capture_output=True)
    redis = next(item for item in storage['items'] if item['kind'] == 'StatefulSet' and item['metadata']['name'] == 'redis')['spec']['template']['spec']['containers'][0]
    server_config = next(item for item in storage['items'] if item['kind'] == 'ConfigMap' and item['metadata']['name'] == 'redis-server-config')['data']['redis.conf']
    with tempfile.TemporaryDirectory(prefix='workshop-redis-config-') as temp:
        configuration = Path(temp) / 'redis.conf'
        configuration.write_text(server_config)
        name = 'workshop-storage-fixture-' + uuid.uuid4().hex[:12]
        try:
            run('docker', 'run', '-d', '--network=none', '--tmpfs', '/data', '--name', name, '-v', f'{configuration}:/etc/redis.conf:ro', redis['image'], 'redis-server', '/etc/redis.conf')
            time.sleep(0.5)
            threshold = run('docker', 'exec', name, 'redis-cli', 'CONFIG', 'GET', 'busy-reply-threshold').stdout.strip().splitlines()
            assert threshold == ['busy-reply-threshold', '100'], threshold
            assert ' * Ready to accept connections' in run('docker', 'logs', name).stdout
        finally:
            subprocess.run(['docker', 'rm', '-fv', name], capture_output=True)
    print('Fresh PostgreSQL initialization and native Redis configuration passed.', flush=True)
    return {'postgresFreshInitialization': True, 'postgresLogPrefix': '%m|%p|%a|%e|', 'postgresNativeSlowQueryLogged': True, 'redisNativeStdoutFormat': True, 'redisSlowScriptThresholdMs': 100}


def validate():
    fixture_env = {'WORKSHOP_ENVIRONMENT': 'fixture', 'WORKSHOP_SESSION_ID': 'fixture', 'SERVICE_VERSION': 'fixture', 'NODE_NAME': 'fixture', 'OTEL_INFRA_LOG_SAMPLE_PERCENT': '5', 'SENTRY_BASE_URL': 'https://us.sentry.io', 'SENTRY_ORG_SLUG': 'fixture', 'SENTRY_AUTH_TOKEN': 'fixture'}
    for service in ['WEB', 'CATALOG', 'FULFILLMENT', 'WORKER', 'INFRASTRUCTURE']:
        fixture_env['SENTRY_OTLP_ENDPOINT_' + service] = 'https://example.invalid/api/1/integration/otlp'
        fixture_env['SENTRY_OTLP_AUTH_' + service] = 'fixture'
    args = ['docker', 'run', '--rm', '--network=none', '-v', f'{ROOT / "infra/collectors"}:/configs:ro']
    for key, value in fixture_env.items():
        args.extend(['-e', key + '=' + value])
    for name in ['agent.json', 'gateway-sentry.json', 'gateway-otlphttp.json']:
        run(*args, IMAGE, 'validate', '--config=/configs/' + name)


def main():
    validate()
    storage_result = verify_storage()
    results = []
    for mode in ['sentry', 'otlphttp']:
        for rate in [5, 10, 100]:
            result = case(mode, rate)
            results.append(result)
            print(f'{mode}, {rate}%: PostgreSQL {result["routineExported"]["pg"]}/2000; Redis {result["routineExported"]["redis"]}/2000; application 20/20; incidents 9/9', flush=True)
    evidence = ROOT / '.local/verification/collector-fixtures.json'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({'collectorImage': IMAGE, 'storage': storage_result, 'cases': results}, indent=2) + '\n')
    os.chmod(evidence, 0o600)
    print('Collector native-log fixtures and both gateway sampling configurations passed.')


if __name__ == '__main__':
    main()
