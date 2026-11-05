#!/usr/bin/env node
// Core-update checks. Run against an OLD WordPress so an update is offered:
//   WMHC_E2E_WP=6.5 WMHC_E2E_PHP=7.4 e2e/start-playground.sh && node e2e/verify-core.mjs
// 1. major update refused while "allow major core updates" is off (default)
// 2. toggles the setting through the real Tools → Hermes Connector form (options.php + nonce)
// 3. runs the core update through POST /updates and checks the new version.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const RUN = join(ROOT, '.e2e-run');
const SAMPLES = join(RUN, 'samples-core');
mkdirSync(SAMPLES, { recursive: true });
const creds = JSON.parse(readFileSync(join(RUN, 'creds.json'), 'utf8'));
const BASE = creds.url.replace(/\/$/, '');
const NS = `${BASE}/wp-json/website-manager-hermes/v1`;
const AUTH = 'Basic ' + Buffer.from(`${creds.user}:${creds.app_password}`).toString('base64');
const redact = (t) => t.split(creds.app_password).join('[REDACTED]');
const save = (n, d) => writeFileSync(join(SAMPLES, `${n}.json`), redact(JSON.stringify(d, null, 2)) + '\n');

const results = [];
let failures = 0;
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};
async function call(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { Authorization: AUTH, Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { console.log(`  (non-JSON ${method} ${url}: HTTP ${res.status}: ${redact(text.slice(0, 300))})`); }
  return { status: res.status, json, text };
}

const upd = await call('GET', `${NS}/updates?refresh=1`);
const offer = (upd.json?.core || [])[0];
save('updates-core-offer', { http_status: upd.status, body: { core: upd.json?.core, counts: upd.json?.counts } });
check('core update offered', !!offer, offer ? `${offer.current_version} -> ${offer.new_version} major=${offer.is_major} allowed=${offer.allowed}` : 'none');
if (!offer) process.exit(1);

const refused = await call('POST', `${NS}/updates`, { type: 'core', items: [offer.new_version] });
save('error-major-core-disabled', { http_status: refused.status, body: refused.json });
if (offer.is_major) {
  check('major core update refused by default (403 wmhc_major_core_updates_disabled)', refused.status === 403 && refused.json?.code === 'wmhc_major_core_updates_disabled', `HTTP ${refused.status}`);
}

// Toggle "allow major core updates" through the admin settings form.
const jar = new Map([['wordpress_test_cookie', 'WP%20Cookie%20check']]);
async function browser(method, url, form) {
  const res = await fetch(url, {
    method, redirect: 'manual',
    headers: { Cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; '), ...(form ? { 'Content-Type': 'application/x-www-form-urlencoded' } : {}) },
    body: form ? new URLSearchParams(form).toString() : undefined,
  });
  for (const c of res.headers.getSetCookie?.() || []) { const [p] = c.split(';'); const i = p.indexOf('='); jar.set(p.slice(0, i), p.slice(i + 1)); }
  return { status: res.status, text: await res.text(), location: res.headers.get('location') };
}
await browser('POST', `${BASE}/wp-login.php`, { log: 'admin', pwd: creds.admin_password || 'password', 'wp-submit': 'Log In', redirect_to: `${BASE}/wp-admin/`, testcookie: '1' });
const page = await browser('GET', `${BASE}/wp-admin/tools.php?page=website-manager-hermes-connector`);
const optNonce = (page.text.match(/name='option_page' value='wmhc_settings_group'[\s\S]*?name="_wpnonce" value="([0-9a-f]+)"/) || [])[1];
check('settings form present with nonce', !!optNonce);
const saved = await browser('POST', `${BASE}/wp-admin/options.php`, {
  option_page: 'wmhc_settings_group', action: 'update', _wpnonce: optNonce,
  _wp_http_referer: '/wp-admin/tools.php?page=website-manager-hermes-connector',
  'wmhc_settings[allow_core_updates]': '1', 'wmhc_settings[allow_major_core_updates]': '1',
});
check('settings saved via options.php (302 back to the page)', saved.status === 302 && (saved.location || '').includes('settings-updated=true'), `HTTP ${saved.status} ${saved.location}`);
const st = await call('GET', `${NS}/status`);
check('status.settings.allow_major_core_updates is now true', st.json?.settings?.allow_major_core_updates === true);

const run = await call('POST', `${NS}/updates`, { type: 'core' });
save('updates-post-core', { http_status: run.status, body: run.json ?? run.text.slice(0, 4000) });
const r = run.json?.results?.[0];
check('POST /updates core succeeded', run.status === 200 && r?.success === true && r?.status === 'updated', r ? `${r.from_version} -> ${r.to_version} ${r.error ?? ''}` : `HTTP ${run.status}`);
check('to_version equals the offered version', r?.to_version === offer.new_version, `${r?.to_version}`);

await new Promise((res) => setTimeout(res, 2000));
for (let i = 0; i < 12; i++) await call('GET', `${NS}/health`); // let Playground workers converge
const st2 = await call('GET', `${NS}/status`);
save('status-after-core', { http_status: st2.status, body: st2.json });
check('status reports the new WordPress version', st2.json?.wp_version === offer.new_version, `${st2.json?.wp_version}`);
const health = await call('GET', `${NS}/health`);
save('health-after-core', { http_status: health.status, body: health.json });
check('health after core update', health.status === 200 && ['ok', 'warning'].includes(health.json?.status), `${health.json?.status} ${JSON.stringify(health.json?.problems)}`);
const home = await fetch(`${BASE}/`);
const homeText = await home.text();
check('front page renders after core update', home.status === 200 && !/critical error/i.test(homeText), `HTTP ${home.status}`);

writeFileSync(join(RUN, 'results-core.json'), JSON.stringify({ failures, results }, null, 2) + '\n');
console.log(`\n${results.length - failures}/${results.length} checks passed`);
process.exit(failures ? 1 : 0);
