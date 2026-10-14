#!/usr/bin/env python3
"""Finite, scoped infrastructure exercises and live Sentry acceptance checks."""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib
import json
import time
import uuid
import workshop as w

sentry = importlib.import_module('verify-sentry')
workflows = importlib.import_module('verify-workflows')
INFRA = 'sentry-otel-workshop-infrastructure'
# TIME is read-only. There is no data mutation, external I/O or open-ended load generator.
REDIS_BUSY_LUA = '''local started = redis.call('TIME')
local start = tonumber(started[1])*1000000 + tonumber(started[2])
local elapsed = 0
for i = 1, 1000000 do
  local now = redis.call('TIME')
  elapsed = tonumber(now[1])*1000000 + tonumber(now[2]) - start
  if elapsed >= 400000 then return elapsed end
end
return redis.error_reply('Workshop clock did not advance within the fixed iteration budget')'''


def stamp(): return datetime.now(timezone.utc).isoformat()


def logs(query, fields=None, limit=100, start=None, end=None):
    return sentry.api('organizations/' + w.CFG['SENTRY_ORG_SLUG'] + '/events/', {
        'dataset': 'logs', **({'start': start, 'end': end or stamp()} if start else {'statsPeriod': '1h'}), 'query': query,
        'field': fields or ['id', 'message', 'trace', 'span_id', 'service.name', 'log.record_id',
                            'resource.k8s.pod.uid', 'resource.k8s.pod.name',
                            'db.postgresql.application_name', 'db.postgresql.sqlstate',
                            'db.postgresql.severity', 'severity'],
        'per_page': limit,
    }).get('data', [])


def count(query):
    rows = logs(query, ['count()'])
    return int(rows[0].get('count()', 0)) if rows else 0


def wait_for(check, description, seconds=None):
    deadline = time.monotonic() + (seconds or int(w.CFG['WORKSHOP_VERIFICATION_TIMEOUT_SECONDS']))
    last = None
    while time.monotonic() < deadline:
        last = check()
        if last: return last
        time.sleep(3)
    raise AssertionError('Timed out: ' + description)


def evidence(name, result):
    w.private_write(w.LOCAL / 'verification' / name, json.dumps(result, indent=2))


def gateway_sampling(percent):
    w.CFG['OTEL_INFRA_LOG_SAMPLE_PERCENT'] = str(percent)
    w.ENV['OTEL_INFRA_LOG_SAMPLE_PERCENT'] = str(percent)
    w.secrets()
    w.collector_config()
    w.kc('-n', w.OBS, 'rollout', 'restart', 'deployment/otel-gateway', capture=True)
    w.kc('-n', w.OBS, 'rollout', 'status', 'deployment/otel-gateway', '--timeout=120s', capture=True)


def emit_batch():
    batch = uuid.uuid4().hex
    routine = f"BEGIN; SET LOCAL log_error_verbosity=terse; SET LOCAL log_min_error_statement=panic; DO $$ BEGIN FOR i IN 1..2000 LOOP RAISE LOG 'Workshop sampling probe {batch} record %', i; END LOOP; END $$; COMMIT;"
    workflows.sql(routine)
    workflows.sql(f"DO $$ BEGIN RAISE WARNING 'Workshop sampling probe {batch} warning'; END $$;")
    failed = w.kc('-n', w.APP, 'exec', 'postgres-0', '--', 'psql', '-U', 'workshop', '-d', 'workshop', '-At', '-v', 'ON_ERROR_STOP=1', '-c',
                  f"DO $$ BEGIN RAISE EXCEPTION 'Workshop sampling probe {batch} error'; END $$;", capture=True, check=False)
    assert failed.returncode != 0, 'Probe exception unexpectedly succeeded'
    return batch


def verify_batch(percent):
    batch = emit_batch()
    base = f'project:{INFRA} resource.service.name:postgres message:"*Workshop sampling probe {batch}*"'
    # A slow DO statement can also contain the batch ID; count only probe bodies.
    routine_query = f'project:{INFRA} resource.service.name:postgres message:"Workshop sampling probe {batch} record *" db.postgresql.severity:LOG'
    wait_for(lambda: count(base + ' db.postgresql.severity:WARNING') == 1 and count(base + ' db.postgresql.severity:ERROR') == 1,
             'native warning/error records in Sentry')
    if percent == 100:
        wait_for(lambda: count(routine_query) == 2000, 'all 2000 native routine records')
        retained = count(routine_query)
    else:
        low, high = {5: (60, 140), 10: (140, 260)}[percent]
        previous_count = None
        stable_reads = 0
        def stable_count():
            nonlocal previous_count, stable_reads
            current = count(routine_query)
            stable_reads = stable_reads + 1 if current == previous_count and low <= current <= high else 0
            previous_count = current
            return current if stable_reads >= 3 else None
        retained = wait_for(stable_count, f'stable {percent}% sampled count in range {low}..{high}')
    result = {'batchId': batch, 'routineGenerated': 2000, 'routineExported': retained,
              'samplePercent': percent, 'warningsGenerated': 1, 'warningsExported': 1,
              'errorsGenerated': 1, 'errorsExported': 1,
              'sentryQuery': base}
    print(f'Infra {percent}%: {retained}/2000 routine logs; warning and error retained', flush=True)
    return result


def compare_sampling():
    previous = w.CFG['OTEL_INFRA_LOG_SAMPLE_PERCENT']
    result = {'timestamp': stamp(), 'exporterMode': w.CFG['SENTRY_EXPORTER_MODE'], 'batches': []}
    try:
        for percent in [100, 5]:
            gateway_sampling(percent)
            result['batches'].append(verify_batch(percent))
            evidence('infra-sampling.json', result)
    finally:
        gateway_sampling(previous)
    result['restoredPercent'] = int(previous)
    evidence('infra-sampling.json', result)
    print('Original sampling setting restored; no generator remains running.', flush=True)
    return result


def redis_busy():
    pod_before = json.loads(w.kc('-n', w.APP, 'get', 'pod', 'redis-0', '-o', 'json', capture=True).stdout)
    configured = workflows.redis('CONFIG', 'GET', 'busy-reply-threshold')
    assert str(configured.get('busy-reply-threshold') if isinstance(configured, dict) else configured[-1]) == '100', configured
    before = stamp()
    elapsed = workflows.redis('EVAL_RO', REDIS_BUSY_LUA, '0')
    assert 400000 <= int(elapsed) < 2000000, elapsed
    assert workflows.redis('PING') == 'PONG'
    script_sha = hashlib.sha1(REDIS_BUSY_LUA.encode()).hexdigest()
    native = w.kc('-n', w.APP, 'logs', 'redis-0', '--since-time=' + before, capture=True).stdout
    assert 'Slow script detected' in native and script_sha in native, 'Redis did not emit native slow-script warning'
    query = f'project:{INFRA} resource.service.name:redis resource.k8s.pod.uid:{pod_before["metadata"]["uid"]} message:"*{script_sha}*"'
    found = wait_for(lambda: logs(query, start=before), 'fresh Redis native warning in Sentry')
    assert any(row.get('severity') in ['warning', 'warn'] for row in found), found
    # Sentry may assign standalone logs a synthetic trace ID. Native Redis
    # records must still have no application span; collector fixtures assert
    # both native OTLP context fields are empty before Sentry ingestion.
    assert all(row.get('span_id') in {None, '', '0' * 16} for row in found), 'Native Redis log has an application span context'
    assert all(row.get('resource.k8s.pod.uid') == pod_before['metadata']['uid'] for row in found)
    pod_after = json.loads(w.kc('-n', w.APP, 'get', 'pod', 'redis-0', '-o', 'json', capture=True).stdout)
    assert pod_after['metadata']['uid'] == pod_before['metadata']['uid']
    assert pod_after['status']['containerStatuses'][0]['restartCount'] == pod_before['status']['containerStatuses'][0]['restartCount']
    result = {'timestamp': before, 'scriptSha': script_sha, 'elapsedMicroseconds': elapsed,
              'redisPodUid': pod_before['metadata']['uid'], 'logs': found, 'recovered': True}
    evidence('redis-busy.json', result)
    print(f'Redis warning retained, script {int(elapsed)/1000:.0f}ms, PING recovered without restart.', flush=True)
    return result


def verify_application_logs(run):
    expected = set()
    for service in ['web', 'catalog', 'fulfillment', 'notification-worker']:
        stdout = w.kc('-n', w.APP, 'logs', 'deployment/' + service, '--since=10m', capture=True).stdout
        for line in stdout.splitlines():
            try: record = json.loads(line)
            except json.JSONDecodeError: continue
            if record.get('workshop.run_id') == run['runId']:
                expected.add(record['log.record_id'])
    assert expected, 'No marked application stdout records'
    # Status polls may have separate trace IDs but carry the same run identity.
    query = 'workshop.run_id:' + run['runId']
    found = wait_for(lambda: (rows if {row.get('log.record_id') for row in rows} >= expected else None)
                     if (rows := logs(query)) else None, 'every marked application log')
    matched = [row for row in found if row.get('log.record_id') in expected]
    native, _ = sentry.unique_log_identities(matched)
    assert len(native) == len(expected), 'Duplicate native identities for application log records'
    return {'generated': len(expected), 'exported': len(native)}


def verify_incident(scenario, run=None):
    run = run or workflows.terminal(sentry.local('/api/runs', {'scenario': scenario}))
    expected = 'failed' if scenario == 'postgres-lock-timeout' else 'completed'
    assert run['status'] == expected, run
    query = f'project:{INFRA} resource.service.name:postgres trace:{run["traceId"]}'
    if scenario == 'postgres-lock-timeout':
        query += ' workshop.infra.evidence:lock_wait'
        assert run['failureService'] == 'catalog' and run['errorCode'] == 'POSTGRES_LOCK_TIMEOUT'
        assert workflows.sql(f"SELECT count(*) FROM fulfillment.orders WHERE run_id='{run['runId']}'") == '0'
        assert workflows.sql("SELECT count(*) FROM pg_locks WHERE relation='catalog.products'::regclass AND mode='AccessExclusiveLock'") == '0'
    else:
        query += ' message:"duration:*"'
    def incident_logs():
        rows = logs(query)
        if scenario == 'postgres-lock-timeout':
            if not any(row.get('db.postgresql.sqlstate') == '55P03' for row in rows): return None
            if not any('still waiting for' in row.get('message', '') for row in rows): return None
        return rows
    found = wait_for(incident_logs, 'correlated native PostgreSQL slow/wait/error logs')
    def incident_spans():
        rows = sentry.api('organizations/' + w.CFG['SENTRY_ORG_SLUG'] + '/events/', {
            'dataset': 'spans', 'statsPeriod': '1h', 'query': 'trace:' + run['traceId'],
            'field': ['span_id', 'span.name', 'span.op', 'span.duration', 'span.status', 'project'], 'per_page': 100,
        }).get('data', [])
        if not any(row.get('span.name') == 'catalog.product_query' for row in rows): return None
        if not any(row.get('span.op') == 'db' and row['project'].endswith('catalog') for row in rows): return None
        if scenario == 'postgres-lock-timeout' and not any(row.get('span.op') == 'db' and row['project'].endswith('catalog') and row.get('span.status') in ['internal_error', 'unknown_error', 'error'] for row in rows): return None
        return rows
    spans = wait_for(incident_spans, 'incident SQL and business spans')
    businesses = {row['span_id'] for row in spans if row.get('span.name') == 'catalog.product_query'}
    assert businesses and all(row.get('span_id') in businesses for row in found), 'Native PG logs do not link to the business span'
    assert all(row.get('resource.k8s.pod.name') == 'postgres-0' and row.get('resource.k8s.pod.uid') for row in found)
    if scenario == 'postgres-lock-timeout':
        assert any(row.get('span.op') == 'db' and row.get('span.status') in ['internal_error', 'unknown_error', 'error'] for row in spans), 'No failed database span'
    app_logs = verify_application_logs(run)
    print(f'{scenario}: native PostgreSQL context and {app_logs["exported"]}/{app_logs["generated"]} application logs verified', flush=True)
    return {'run': run, 'nativePostgresLogs': found, 'applicationLogs': app_logs}


def verify():
    result = {'timestamp': stamp(), 'exporterMode': w.CFG['SENTRY_EXPORTER_MODE'],
              'samplePercent': int(w.CFG['OTEL_INFRA_LOG_SAMPLE_PERCENT']),
              'verificationTimeoutSeconds': int(w.CFG['WORKSHOP_VERIFICATION_TIMEOUT_SECONDS'])}
    # Start the bounded incident matrix first so visibility delay is shared.
    incidents = [(name, workflows.terminal(sentry.local('/api/runs', {'scenario': name})))
                 for name in ['slow-postgres', 'postgres-lock-timeout']]
    result['sampling'] = verify_batch(result['samplePercent'])
    result['incidents'] = [verify_incident(name, run) for name, run in incidents]
    result['redisBusy'] = redis_busy()
    healthy = workflows.terminal(sentry.local('/api/runs', {'scenario': 'full-journey'}))
    assert healthy['status'] == 'completed'
    result['healthyRecovery'] = {**healthy, 'applicationLogs': verify_application_logs(healthy)}
    evidence('infra-live.json', result)
    print('Infrastructure sampling, native log correlation, complete application logs and recovery passed.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sampling', 'redis-busy', 'verify'])
    args = parser.parse_args()
    w.require(['SENTRY_AUTH_TOKEN', 'SENTRY_ORG_SLUG', 'SENTRY_BASE_URL'])
    w.forward()
    {'sampling': compare_sampling, 'redis-busy': redis_busy, 'verify': verify}[args.command]()


if __name__ == '__main__': main()
