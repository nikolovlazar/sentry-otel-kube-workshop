#!/usr/bin/env node
// Builds own source-map generation and upload; credentials exist only in the upload child.
import { existsSync, readFileSync, mkdirSync, writeFileSync, chmodSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const components = {
  web: { directory: 'apps/web', compiler: 'next', args: ['build'], artifacts: ['.next/static', '.next/server'], project: 'sentry-otel-workshop-web' },
  'notification-worker': { directory: 'services/notification-worker', compiler: 'tsc', args: [], artifacts: ['dist'], project: 'sentry-otel-workshop-worker' },
};
const credentialKeys = ['SENTRY_AUTH_TOKEN', 'SENTRY_SOURCE_MAP_AUTH_TOKEN', 'SOURCE_MAP_AUTH_TOKEN'];
const buildKeys = ['SENTRY_RELEASE', 'SERVICE_VERSION', 'SENTRY_ORG_SLUG', 'SENTRY_ORG', 'SENTRY_PROJECT_SLUG', 'SENTRY_PROJECT', 'SENTRY_BASE_URL', 'SENTRY_URL', 'SENTRY_BUILD_UPLOAD_REQUIRED', 'SENTRY_UPLOAD_SOURCE_MAPS'];

export function buildEnvironment(component, initial = { ...process.env }, dotenvPath = resolve(root, '.env'), secretPath = '/run/secrets/sentry_source_map_token') {
  const spec = components[component];
  if (!spec) throw new Error('Choose web or notification-worker.');
  // Node's dotenv parser handles quoted values/comments without mutating caller env.
  const privateConfig = existsSync(dotenvPath) ? parseEnv(readFileSync(dotenvPath, 'utf8')) : {};
  const loaded = { ...Object.fromEntries(buildKeys.filter(key => privateConfig[key] !== undefined).map(key => [key, privateConfig[key]])), ...initial };
  const token = existsSync(secretPath) ? readFileSync(secretPath, 'utf8').trim()
    : initial.SENTRY_SOURCE_MAP_AUTH_TOKEN || initial.SOURCE_MAP_AUTH_TOKEN || initial.SENTRY_AUTH_TOKEN
      || privateConfig.SENTRY_SOURCE_MAP_AUTH_TOKEN || privateConfig.SOURCE_MAP_AUTH_TOKEN;
  const revision = spawnSync('git', ['rev-parse', '--verify', 'HEAD'], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  loaded.SERVICE_VERSION ||= loaded.SENTRY_RELEASE?.replace(/^sentry-otel-workshop@/, '') || (revision.status === 0 ? revision.stdout.trim() : 'dev-uncommitted');
  loaded.SENTRY_RELEASE ||= 'sentry-otel-workshop@' + loaded.SERVICE_VERSION;
  loaded.SENTRY_ORG_SLUG = initial.SENTRY_ORG_SLUG || initial.SENTRY_ORG || privateConfig.SENTRY_ORG_SLUG || privateConfig.SENTRY_ORG;
  loaded.SENTRY_PROJECT_SLUG = initial.SENTRY_PROJECT_SLUG || initial.SENTRY_PROJECT || privateConfig.SENTRY_PROJECT_SLUG || privateConfig.SENTRY_PROJECT || spec.project;
  loaded.SENTRY_BASE_URL = initial.SENTRY_BASE_URL || initial.SENTRY_URL || privateConfig.SENTRY_BASE_URL || privateConfig.SENTRY_URL || 'https://sentry.io';
  const required = ['1', 'true'].includes(loaded.SENTRY_BUILD_UPLOAD_REQUIRED);
  const disabled = ['0', 'false'].includes(loaded.SENTRY_UPLOAD_SOURCE_MAPS);
  if (required && disabled) throw new Error('Required source-map upload cannot be disabled.');
  if (required && !token) throw new Error('Required source-map upload token is missing. Configure SENTRY_SOURCE_MAP_AUTH_TOKEN or provide the BuildKit secret.');
  const upload = Boolean(token) && !disabled;
  if (upload && !loaded.SENTRY_ORG_SLUG) throw new Error('Set SENTRY_ORG_SLUG (or SENTRY_ORG) for source-map upload.');
  // The SDK build plugin needs only public release/routing configuration to inject IDs.
  for (const key of credentialKeys) delete loaded[key];
  delete loaded.SENTRY_DSN;
  return { spec, compilerEnv: loaded, token, upload, disabled };
}

function command(executable, args, options = {}) {
  const result = spawnSync(executable, args, options);
  if (result.error) throw new Error('Could not start build command: ' + result.error.code);
  if (result.status !== 0) throw new Error('Build command failed with exit status ' + result.status + '.');
  return result;
}

function build(component) {
  const settings = buildEnvironment(component);
  const { spec, compilerEnv, token, upload, disabled } = settings;
  const directory = resolve(root, spec.directory);
  const binary = name => resolve(directory, 'node_modules/.bin', name);
  command(binary(spec.compiler), spec.args, { cwd: directory, env: compilerEnv, stdio: 'inherit' });
  if (component === 'notification-worker') command(binary('sentry-cli'), ['sourcemaps', 'inject', ...spec.artifacts], { cwd: directory, env: compilerEnv, stdio: 'inherit' });
  if (!upload) {
    console.log('Sentry source-map upload skipped: ' + (disabled ? 'explicitly disabled.' : 'no upload token configured.'));
    return;
  }
  const uploadEnv = { ...compilerEnv, SENTRY_AUTH_TOKEN: token, SENTRY_ORG: compilerEnv.SENTRY_ORG_SLUG, SENTRY_PROJECT: compilerEnv.SENTRY_PROJECT_SLUG, SENTRY_URL: compilerEnv.SENTRY_BASE_URL };
  const result = spawnSync(binary('sentry-cli'), ['sourcemaps', 'upload', ...spec.artifacts, '--release', compilerEnv.SENTRY_RELEASE, '--no-rewrite', '--wait-for', '120'], { cwd: directory, env: uploadEnv, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  const details = ((result.stdout || '') + (result.stderr || '')).replaceAll(token, '[REDACTED]');
  const logs = resolve(root, '.local/verification');
  mkdirSync(logs, { recursive: true });
  const logPath = resolve(logs, 'build-source-maps-' + component + '.log');
  writeFileSync(logPath, details, { mode: 0o600 });
  chmodSync(logPath, 0o600);
  if (result.error || result.status !== 0 || !details.toLowerCase().includes('processing complete')) {
    console.error('Sentry source-map upload diagnostics (' + component + '):\n' + (details.trim() || 'The CLI did not emit diagnostic output.'));
    throw new Error('Sentry source-map upload or processing failed. See .local/verification/build-source-maps-' + component + '.log; the build is incomplete.');
  }
  console.log('Sentry processed ' + component + ' source maps for the configured release.');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { build(process.argv[2]); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
