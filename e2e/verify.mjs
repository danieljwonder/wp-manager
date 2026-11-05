#!/usr/bin/env node
// End-to-end checks for the connector against the Playground site started by
// e2e/start-playground.sh. Node 18+ (global fetch), no dependencies.
//
// Usage: node e2e/verify.mjs
// Writes response samples to .e2e-run/samples/*.json and a summary to
// .e2e-run/results.json. The Application Password never appears in samples.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const RUN = join(ROOT, '.e2e-run');
const LABEL = process.env.WMHC_E2E_LABEL ? `-${process.env.WMHC_E2E_LABEL}` : '';
const SAMPLES = join(RUN, `samples${LABEL}`);
mkdirSync(SAMPLES, { recursive: true });

const creds = JSON.parse(readFileSync(join(RUN, 'creds.json'), 'utf8'));
const BASE = creds.url.replace(/\/$/, '');
const NS = `${BASE}/wp-json/website-manager-hermes/v1`;
const ABIL = `${BASE}/wp-json/wp-abilities/v1`;
const SECRETS = [creds.app_password, creds.app_password.replace(/ /g, '')];
const auth = (user, pw) => 'Basic ' + Buffer.from(`${user}:${pw}`).toString('base64');
const AUTH = auth(creds.user, creds.app_password);

const results = [];
let failures = 0;
function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail });
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

function redact(text) {
  let out = text;
  for (const s of SECRETS) if (s) out = out.split(s).join('[REDACTED]');
  return out;
}

function save(name, data) {
  writeFileSync(join(SAMPLES, `${name}.json`), redact(JSON.stringify(data, null, 2)) + '\n');
}

async function call(method, url, { body, headers = {}, authHeader = AUTH } = {}) {
  const h = { Accept: 'application/json', ...headers };
  if (authHeader) h.Authorization = authHeader;
  if (body !== undefined) h['Content-Type'] = 'application/json';
  const res = await fetch(url, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  if (json === null) {
    console.log(`  (non-JSON response from ${method} ${url}: HTTP ${res.status}, ${text.length} bytes: ${redact(text.slice(0, 400))})`);
  }
  return { status: res.status, json, text };
}

// Playground runs several PHP workers whose in-memory filesystems are synced
// asynchronously. Right after an update run removes /wordpress/.maintenance,
// another worker can briefly still see it (file_exists() true, require fails
// => fatal). Real servers don't have this; wait for the workers to converge.
const quirks = [];
async function settle(label) {
  await new Promise((r) => setTimeout(r, 1500));
  let ok = 0;
  for (let i = 0; i < 40 && ok < 12; i++) {
    const h = await call('GET', `${NS}/health`);
    if (h.json) ok++; else { ok = 0; quirks.push(`${label}: stale .maintenance seen by a worker (HTTP ${h.status})`); await new Promise((r) => setTimeout(r, 500)); }
  }
}

const find = (arr, key, val) => (arr || []).find((x) => x[key] === val);

// ---------------------------------------------------------------- status
const status = await call('GET', `${NS}/status`);
save('status', { http_status: status.status, body: status.json });
check('GET /status 200', status.status === 200, `HTTP ${status.status}`);
check('status.connector_version is 0.1.0', status.json?.connector_version === '0.1.0');
check('status.environment_type is local', status.json?.environment_type === 'local');
const wpVer = (status.json?.wp_version || '0').split('.').map(Number);
const HAS_ABILITIES = wpVer[0] > 6 || (wpVer[0] === 6 && wpVer[1] >= 9);
check(`status.abilities_api ${HAS_ABILITIES} (WP ${status.json?.wp_version})`, status.json?.abilities_api === HAS_ABILITIES);

// ---------------------------------------------------------------- updates?refresh=1
const upd = await call('GET', `${NS}/updates?refresh=1`);
save('updates-refresh', { http_status: upd.status, body: upd.json });
check('GET /updates?refresh=1 200', upd.status === 200);
check('refresh had no api.wordpress.org errors', Array.isArray(upd.json?.refresh_errors) && upd.json.refresh_errors.length === 0, JSON.stringify(upd.json?.refresh_errors));
const ce = find(upd.json?.plugins, 'file', 'classic-editor/classic-editor.php');
const hd = find(upd.json?.plugins, 'file', 'hello-dolly/hello.php');
const tt = find(upd.json?.themes, 'stylesheet', 'twentytwentyfour');
check('classic-editor update pending (old version installed)', ce && ce.current_version === '1.6.3' && ce.new_version && ce.new_version !== '1.6.3', ce ? `${ce.current_version} -> ${ce.new_version}` : 'missing');
check('hello-dolly update pending', hd && hd.current_version === '1.6', hd ? `${hd.current_version} -> ${hd.new_version}` : 'missing');
check('twentytwentyfour update pending', tt && tt.current_version === '1.0', tt ? `${tt.current_version} -> ${tt.new_version}` : 'missing');

// ---------------------------------------------------------------- lock + plugin update
const pluginBody = { type: 'plugin', items: ['classic-editor/classic-editor.php', 'hello-dolly/hello.php', 'not-installed/nope.php'] };
const pluginRun = call('POST', `${NS}/updates`, { body: pluginBody });
await new Promise((r) => setTimeout(r, 600));
const concurrent = await call('POST', `${NS}/updates`, { body: { type: 'theme' } });
save('updates-post-locked', { http_status: concurrent.status, body: concurrent.json });
check('second concurrent POST /updates is refused with 409 wmhc_locked', concurrent.status === 409 && concurrent.json?.code === 'wmhc_locked', `HTTP ${concurrent.status} ${concurrent.json?.code ?? ''}`);
const plug = await pluginRun;
save('updates-post-plugin', { http_status: plug.status, request: pluginBody, body: plug.json });
check('POST /updates plugin 200', plug.status === 200, `HTTP ${plug.status}`);
const rce = find(plug.json?.results, 'item', 'classic-editor/classic-editor.php');
const rhd = find(plug.json?.results, 'item', 'hello-dolly/hello.php');
const rno = find(plug.json?.results, 'item', 'not-installed/nope.php');
check('classic-editor updated, version changed', rce?.success && rce.status === 'updated' && rce.from_version === '1.6.3' && rce.to_version === ce?.new_version, rce ? `${rce.from_version} -> ${rce.to_version}` : 'missing');
check('classic-editor still active after update', rce?.active_before === true && rce?.active_after === true && rce?.reactivated === null);
check('hello-dolly updated and still inactive', rhd?.success && rhd.to_version === hd?.new_version && rhd.active_after === false, rhd ? `${rhd.from_version} -> ${rhd.to_version}` : 'missing');
check('unknown plugin reported not_installed', rno && rno.success === false && rno.status === 'not_installed');
check('overall result partial (one bad item)', plug.json?.result === 'partial' && plug.json?.success === false);
check('maintenance mode off after run', plug.json?.maintenance_off === true);

// Independent check through core's own REST API.
await settle('after plugin run');
const corePlugins = await call('GET', `${BASE}/wp-json/wp/v2/plugins`);
const cp = (corePlugins.json || []).find((p) => p.plugin === 'classic-editor/classic-editor');
check('core /wp/v2/plugins confirms classic-editor new version + active', cp && cp.version === ce?.new_version && cp.status === 'active', cp ? `${cp.version} ${cp.status}` : `HTTP ${corePlugins.status}`);

// ---------------------------------------------------------------- theme update
const themeRun = await call('POST', `${NS}/updates`, { body: { type: 'theme', items: [] } });
save('updates-post-theme', { http_status: themeRun.status, request: { type: 'theme', items: [] }, body: themeRun.json });
await settle('after theme run');
const rtt = find(themeRun.json?.results, 'item', 'twentytwentyfour');
check('POST /updates theme (all) updated twentytwentyfour', themeRun.status === 200 && rtt?.success && rtt.from_version === '1.0' && rtt.to_version === tt?.new_version, rtt ? `${rtt.from_version} -> ${rtt.to_version}` : `HTTP ${themeRun.status}`);
check('twentytwentyfour still the active theme', rtt?.active_before === true && rtt?.active_after === true);
const coreThemes = await call('GET', `${BASE}/wp-json/wp/v2/themes?status=active`);
const ct = (coreThemes.json || [])[0];
check('core /wp/v2/themes confirms active theme version', ct && ct.stylesheet === 'twentytwentyfour' && ct.version === tt?.new_version, ct ? `${ct.stylesheet} ${ct.version}` : `HTTP ${coreThemes.status}`);

// ---------------------------------------------------------------- after
const after = await call('GET', `${NS}/updates`);
save('updates-after', { http_status: after.status, body: after.json });
const stillPending = [...(after.json?.plugins || []).map((p) => p.file), ...(after.json?.themes || []).map((t) => t.stylesheet)]
  .filter((x) => ['classic-editor/classic-editor.php', 'hello-dolly/hello.php', 'twentytwentyfour'].includes(x));
check('updated items no longer pending (update data refreshed)', after.status === 200 && stillPending.length === 0, `counts ${JSON.stringify(after.json?.counts)}`);

const core = await call('POST', `${NS}/updates`, { body: { type: 'core' } });
save('updates-post-core-uptodate', { http_status: core.status, body: core.json ?? core.text.slice(0, 4000) });
check('POST /updates core (no minor pending) => up_to_date, or skipped when only a major is offered', core.status === 200 && ['up_to_date', 'skipped'].includes(core.json?.results?.[0]?.status), core.json?.results?.[0]?.status);
const coreBad = await call('POST', `${NS}/updates`, { body: { type: 'core', items: ['1.0'] } });
save('error-core-version-not-offered', { http_status: coreBad.status, body: coreBad.json });
check('POST core with a version not offered => 404 wmhc_no_core_update', coreBad.status === 404 && coreBad.json?.code === 'wmhc_no_core_update');
const tr = await call('POST', `${NS}/updates`, { body: { type: 'translations' } });
save('updates-post-translations', { http_status: tr.status, body: tr.json });
const pendingTr = upd.json?.translations || [];
check(`POST /updates translations runs (${pendingTr.length} pending at start)`, tr.status === 200 && tr.json?.success === true && (tr.json?.results || []).every((x) => x.success && ['updated', 'up_to_date'].includes(x.status)), (tr.json?.results || []).map((x) => `${x.item}:${x.status}`).join(', '));
await settle('after translations run');
const afterTr = await call('GET', `${NS}/updates`);
save('updates-after-translations', { http_status: afterTr.status, body: afterTr.json });
check('update data still populated after a translations run (core clears the transients)', afterTr.json?.last_checked?.plugins && afterTr.json?.last_checked?.themes && afterTr.json?.last_checked?.core, JSON.stringify(afterTr.json?.last_checked));

const health = await call('GET', `${NS}/health`);
save('health', { http_status: health.status, body: health.json });
check('GET /health ok', health.status === 200 && health.json?.status === 'ok' && health.json?.database?.reachable === true, `status=${health.json?.status}`);

const log = await call('GET', `${NS}/log`);
save('log', { http_status: log.status, body: log.json });
const runs = (log.json?.entries || []).filter((e) => e.action === 'updates.run');
check('GET /log has the plugin and theme runs', runs.some((e) => e.type === 'plugin') && runs.some((e) => e.type === 'theme'), `${log.json?.count} entries`);
check('log never contains the app password', !SECRETS.some((s) => log.text.includes(s)));

// ---------------------------------------------------------------- errors
const noAuth = await call('GET', `${NS}/status`, { authHeader: null });
save('error-unauthenticated', { http_status: noAuth.status, body: noAuth.json });
check('unauthenticated /status => 401', noAuth.status === 401, `HTTP ${noAuth.status} ${noAuth.json?.code}`);
const badPw = await call('GET', `${NS}/status`, { authHeader: auth(creds.user, 'abcd efgh ijkl mnop qrst uvwx') });
save('error-wrong-password', { http_status: badPw.status, body: badPw.json });
check('wrong app password => 401', badPw.status === 401, `HTTP ${badPw.status} ${badPw.json?.code}`);
const badType = await call('POST', `${NS}/updates`, { body: { type: 'bogus' } });
save('error-invalid-type', { http_status: badType.status, body: badType.json });
check('invalid type => 400 rest_invalid_param', badType.status === 400 && badType.json?.code === 'rest_invalid_param');

// ---------------------------------------------------------------- abilities
if (!HAS_ABILITIES) {
  const none = await call('GET', `${ABIL}/abilities`);
  check('no Abilities API on this WP: route absent, connector still loads', none.status === 404, `HTTP ${none.status}`);
} else {
  const abil = await call('GET', `${ABIL}/abilities?per_page=100`);
  const ours = (abil.json || []).filter((a) => a.name?.startsWith('website-manager-hermes/'));
  save('abilities-list', { http_status: abil.status, body: ours });
  check('abilities list has get-status, list-updates, run-updates', ['get-status', 'list-updates', 'run-updates'].every((n) => ours.some((a) => a.name === `website-manager-hermes/${n}`)), ours.map((a) => a.name).join(', '));
  const abStatus = await call('GET', `${ABIL}/abilities/website-manager-hermes/get-status/run`);
  check('ability get-status runs (GET)', abStatus.status === 200 && abStatus.json?.connector_version === '0.1.0');
  const abList = await call('GET', `${ABIL}/abilities/website-manager-hermes/list-updates/run?input%5Brefresh%5D=false`);
  save('ability-list-updates', { http_status: abList.status, body: abList.json });
  check('ability list-updates runs (GET)', abList.status === 200 && abList.json?.counts !== undefined);
  const abRun = await call('POST', `${ABIL}/abilities/website-manager-hermes/run-updates/run`, { body: { input: { type: 'plugin', items: ['hello-dolly/hello.php'] } } });
  save('ability-run-updates', { http_status: abRun.status, body: abRun.json });
  check('ability run-updates runs (POST) => up_to_date', abRun.status === 200 && abRun.json?.results?.[0]?.status === 'up_to_date');
  await settle('after ability run');
  const abAnon = await call('GET', `${ABIL}/abilities/website-manager-hermes/get-status/run`, { authHeader: null });
  check('ability refuses anonymous callers', abAnon.status === 401 || abAnon.status === 403, `HTTP ${abAnon.status}`);
}

// ---------------------------------------------------------------- admin page + connection string
const jar = new Map();
function storeCookies(res) {
  for (const c of res.headers.getSetCookie?.() || []) {
    const [pair] = c.split(';');
    const i = pair.indexOf('=');
    jar.set(pair.slice(0, i), pair.slice(i + 1));
  }
}
const cookieHeader = () => [...jar].map(([k, v]) => `${k}=${v}`).join('; ');
async function browser(method, url, form) {
  const res = await fetch(url, {
    method,
    redirect: 'manual',
    headers: { Cookie: cookieHeader(), ...(form ? { 'Content-Type': 'application/x-www-form-urlencoded' } : {}) },
    body: form ? new URLSearchParams(form).toString() : undefined,
  });
  storeCookies(res);
  return { status: res.status, text: await res.text(), location: res.headers.get('location') };
}
jar.set('wordpress_test_cookie', 'WP%20Cookie%20check');
const login = await browser('POST', `${BASE}/wp-login.php`, { log: 'admin', pwd: creds.admin_password || 'password', 'wp-submit': 'Log In', redirect_to: `${BASE}/wp-admin/`, testcookie: '1' });
check('wp-admin login (cookie)', login.status === 302 && [...jar.keys()].some((k) => k.startsWith('wordpress_logged_in')), `HTTP ${login.status}`);
const PAGE = `${BASE}/wp-admin/tools.php?page=website-manager-hermes-connector`;
const page = await browser('GET', PAGE);
check('Tools → Hermes Connector page renders', page.status === 200 && page.text.includes('Hermes Connector') && page.text.includes('Create connection for Hermes'), `HTTP ${page.status}`);
check('admin menu has Tools → Hermes Connector link', page.text.includes('tools.php?page=website-manager-hermes-connector'));
check('page shows recent actions table with updates.run', page.text.includes('updates.run'));
const nonce = (page.text.match(/name="_wpnonce" value="([0-9a-f]+)"/) || [])[1];
check('create-connection form has a nonce', !!nonce);
const noNonce = await browser('POST', PAGE, { wmhc_action: 'create_connection', _wpnonce: 'deadbeef' });
check('create connection with a bad nonce is rejected', noNonce.status === 403 && !noNonce.text.includes('wpmh1:'), `HTTP ${noNonce.status}`);
const created = await browser('POST', PAGE, { wmhc_action: 'create_connection', _wpnonce: nonce, _wp_http_referer: '/wp-admin/tools.php?page=website-manager-hermes-connector' });
const conn = (created.text.match(/(wpmh1:[A-Za-z0-9+/=]+)/) || [])[1];
check('create connection shows a wpmh1: connection string', created.status === 200 && !!conn, `HTTP ${created.status}`);
let decoded = null;
if (conn) {
  decoded = JSON.parse(Buffer.from(conn.slice(6), 'base64').toString('utf8'));
  check('connection string decodes to {v:1,url,user,app_password}', decoded.v === 1 && decoded.url === BASE && decoded.user === 'admin' && /^[A-Za-z0-9]{4}( [A-Za-z0-9]{4}){5}$/.test(decoded.app_password), `keys: ${Object.keys(decoded).join(',')}`);
  const viaConn = await call('GET', `${NS}/status`, { authHeader: auth(decoded.user, decoded.app_password) });
  check('connection string credentials authenticate /status', viaConn.status === 200);
  save('connection-string-decoded', { prefix: 'wpmh1:', decoded: { ...decoded, app_password: '[REDACTED]' } });
  SECRETS.push(decoded.app_password, decoded.app_password.replace(/ /g, ''));
}
const page2 = await browser('GET', PAGE);
check('page lists the existing Hermes connection (no secret shown again)', page2.text.includes('Hermes (website-manager)') && !page2.text.includes('wpmh1:'));
const log2 = await call('GET', `${NS}/log`);
check('log records connection.create without the secret', (log2.json?.entries || []).some((e) => e.action === 'connection.create') && !SECRETS.some((s) => s && log2.text.includes(s)));

// ---------------------------------------------------------------- done
if (quirks.length) console.log(`\nPlayground quirks observed (not failures):\n  ${quirks.join('\n  ')}`);
writeFileSync(join(RUN, `results${LABEL}.json`), JSON.stringify({ base: BASE, wp_version: status.json?.wp_version, php_version: status.json?.php_version, failures, quirks, results }, null, 2) + '\n');
console.log(`\n${results.length - failures}/${results.length} checks passed`);
process.exit(failures ? 1 : 0);
