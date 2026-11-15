// Loads the rewritten desktop/plugin.js (imports pointed at stubs.mjs), registers it against a fake
// ctx, and renders every screen with fixture data. Prints "OK <n> renders" or throws.
import * as stubs from './stubs.mjs'

const pluginUrl = process.argv[2]
const mod = await import(pluginUrl)
const plugin = mod.default
if (plugin.id !== 'wp-manager') throw new Error('plugin id must be wp-manager (folder name)')

const contributions = []
const ctx = {
  source: 'plugin:wp-manager',
  rest: async (path, opts) => { stubs.calls.rest.push([path, opts]); return {} },
  registerMany: cs => { contributions.push(...cs); return () => {} },
  register: c => { contributions.push(c); return () => {} },
  onEvent: () => () => {},
  onDispose: () => {},
  os: { openExternal: async () => true }
}
plugin.register(ctx)

const byArea = area => contributions.filter(c => c.area === area)
const route = byArea('routes')[0]
const nav = byArea('sidebar.nav')[0]
const palette = byArea('palette')
if (!route || route.data.path !== '/wp-manager') throw new Error('route /wp-manager missing')
if (!nav || nav.data.label !== 'WP Manager' || !nav.data.codicon) throw new Error('sidebar nav missing')
if (!palette.some(p => p.data.label === 'WP Manager: Scan all sites')) throw new Error('palette scan command missing')

const site = {
  id: 'wp-example', label: 'Example', url: 'https://wp.example.test', admin_url: 'https://wp.example.test/wp-admin/',
  tags: ['clients'], insecure: false, wp_version: '6.8.1', php_version: '8.2.20', connector_version: '0.1.0',
  pending: { plugins: 2, themes: 1, core: 1, translations: 1, total: 5 },
  health: { verdict: 'degraded', failing: ['plugin paused'], checked_at: '2026-11-05T09:00:00Z' },
  last_checked: '2026-11-05T09:00:00Z', last_error: null
}
const detail = {
  ...site,
  last_error: null,
  updates: {
    plugins: [{ id: 'hello-dolly/hello.php', name: 'Hello Dolly', current_version: '1.6', new_version: '1.7.2', compatible: true }],
    themes: [{ id: 'twentytwentyone', name: 'Twenty Twenty-One', current_version: '1.8', new_version: '2.5', compatible: false }],
    core: [{ id: '6.8.3', name: 'WordPress', current_version: '6.8.1', new_version: '6.8.3', is_major: false, allowed: false }],
    translations: [{ id: 'plugin:hello-dolly:de_DE', name: 'hello-dolly', new_version: '1.7.2', language: 'de_DE' }]
  },
  health_detail: { verdict: 'ok', failing: [], checked_at: '2026-11-05T09:00:00Z' },
  recent_actions: [{ time: '2026-11-05T09:00:00Z', actor: 'agent', action: 'update_apply', type: 'plugin', items: ['a/a.php'], result: 'ok', health: 'ok' }]
}

let renders = 0
function renderPage(restData, overrides = {}, viewport) {
  stubs.state.restData = restData
  stubs.state.overrides = overrides
  if (viewport) stubs.host.state.viewport = { get: () => viewport, subscribe: () => () => {} }
  stubs.render(route.render())
  renders++
}

renderPage({})                                                      // loading
renderPage({ sites: { sites: [], tags: [] } })                      // empty
renderPage({ sites: { sites: [site, { ...site, id: 'b', health: null, pending: {}, last_error: { message: 'x' } }], tags: ['clients'] } }) // table
renderPage({ sites: { sites: [site], tags: ['clients'] } }, { Page: { 0: 'zzz' } })   // no match
renderPage({ sites: { sites: [site], tags: ['clients'] } }, { Page: { 1: 'clients' } }) // tag filter
renderPage({ sites: { sites: [site], tags: [] } }, {}, { width: 400, height: 800, narrow: true }) // cards
const open = { 'sites': { sites: [site], tags: [] }, 'site/wp-example': detail }
renderPage(open, { Page: { 2: 'wp-example' } })                                       // detail dialog
renderPage(open, { Page: { 2: 'wp-example' }, SiteDialog: { 1: { batches: [['plugin', ['hello-dolly/hello.php']]] }, 2: true,
  3: [{ type: 'plugin', update_result: 'success', health: { verdict: 'down' }, alert: 'HEALTH CHECK DOWN', warning: 'x',
        results: [{ item: 'a', success: false, error: 'e' }] }] } })                   // confirm + results
renderPage({ sites: { sites: [], tags: [] }, 'site/wp-example': undefined }, { Page: { 2: 'wp-example' } }) // detail loading
for (const [mode, cred] of [['string', 'env'], ['manual', 'env'], ['manual', 'op']]) {
  renderPage({ sites: { sites: [], tags: [] } }, { Page: { 3: true }, AddSiteDialog: { 0: mode, 3: cred,
    4: { connection_string: '', url: 'http://shop.example.com', username: 'u', label: '', tags: '', env: '', op: '', allow_insecure: false } } })
}
renderPage({ sites: { sites: [], tags: [] } }, { Page: { 3: true }, AddSiteDialog: { 5: {
  site: { label: 'Example' }, verified: { wp_version: '6.8.1' }, credential_storage: 'stored', note: 'n', warning: 'w' } } })

// update-alert status line: off, on, on with a failing site
const alertText = []
for (const watch of [{ set_up: false, scope: 'not set up' },
  { set_up: true, scope: 'all sites', last_run: '2026-11-05T09:00:00Z', last_result: { new: 2 }, failing: {} },
  { set_up: true, scope: 'all sites', last_run: null, last_result: null, failing: { 'wp-example': 'credential' } }]) {
  stubs.calls.text.length = 0
  renderPage({ sites: { sites: [site], tags: [] }, watch })
  alertText.push(stubs.calls.text.join(' '))
}
if (!stubs.calls.queries.some(q => q.queryKey.includes('watch'))) throw new Error('watch status not queried')
if (!/Update alerts are off/.test(alertText[0])) throw new Error('alerts-off hint missing')
if (!/Set up alerts in chat/.test(alertText[0])) throw new Error('alerts setup button missing')
if (!/Update alerts: all sites/.test(alertText[1]) || !/2 new/.test(alertText[1])) throw new Error('alerts-on line missing')
if (!/1 site failing/.test(alertText[2])) throw new Error('failing badge missing')

// palette scan runs outside React
await palette.find(p => p.data.id === 'wp-manager.scan').data.run()
await new Promise(r => setTimeout(r, 10))
if (!stubs.calls.rest.some(([p]) => p === '/scan')) throw new Error('scan command did not call /scan')

// alerts command drafts the request into the composer (never sends) and goes to the chat
await palette.find(p => p.data.id === 'wp-manager.alerts').data.run()
if (!stubs.calls.drafts.length || !/update alerts/i.test(stubs.calls.drafts[0][1])) throw new Error('alerts command did not draft the prompt')
if (!stubs.calls.navigate.includes('/')) throw new Error('alerts command did not open the chat')
// without host.composer it degrades to a hint toast
const savedComposer = stubs.host.composer; delete stubs.host.composer
await palette.find(p => p.data.id === 'wp-manager.alerts').data.run()
if (!stubs.calls.notify.some(n => /Ask Hermes in chat/.test(n.message))) throw new Error('alerts fallback toast missing')
stubs.host.composer = savedComposer
console.log(`OK ${renders} renders`)
