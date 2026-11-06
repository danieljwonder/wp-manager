// Deterministic query lifecycle tests: execute the real query function with a deferred scan.
import assert from 'node:assert/strict'
import * as s from './stubs.mjs'
const plugin = (await import(process.argv[2])).default
let route
let responder
plugin.register({
  rest: async (path, opts) => { s.calls.rest.push([path, opts]); return responder(path, opts) },
  registerMany: rows => { route = rows.find(r => r.area === 'routes') },
  onEvent: () => {}, onDispose: () => {}, os: {}
})
const empty = { plugins: [], themes: [], core: [], translations: [] }
const summary = { id: 'new-site', label: 'New site', url: 'https://example.test', pending: { plugins: 1, themes: 3, total: 4 }, last_checked: null }
const fresh = { ...summary, updates: empty, health_detail: null }
const loaded = { ...fresh, last_checked: '2026-11-06T00:00:00Z', updates: { ...empty,
  plugins: [{ id: 'a/a.php', name: 'Plugin A' }], themes: [1, 2, 3].map(i => ({ id: `theme-${i}`, name: `Theme ${i}` })) } }
let renders = 0
function render(data, error = null, selected = {}) {
  s.calls.nodes.length = s.calls.text.length = s.calls.queries.length = s.calls.mutations.length = 0
  s.state.restData = { sites: { sites: [summary], tags: [] }, 'site/new-site': data }
  s.state.queryStates = error ? { 'site/new-site': { isError: true, error } } : {}
  s.state.overrides = { Page: { 2: 'new-site' }, SiteDialog: { 0: selected } }
  s.render(route.render()); renders++
  return s.calls.queries.find(q => q.queryKey[2] === 'site')
}
const text = () => s.calls.text.join(' ')
const button = name => s.calls.nodes.find(n => n.type.name === 'Button' && n.props.children === name)
const guarded = () => {
  assert.equal(button('Update all').props.disabled, true)
  assert.equal(button('Update selected').props.disabled, true)
  assert.doesNotMatch(text(), /Everything is up to date/)
}
render(fresh)
guarded() // original bug: fabricated empty arrays were rendered as an all-clear
let q = render(undefined)
guarded()
assert.match(text(), /Loading pending updates/)
assert.ok(s.calls.nodes.some(n => n.type.name === 'Pending' && n.props.pending.total === 4), 'summary counts visible while loading')
let release
const scan = new Promise(resolve => { release = resolve })
let hydrated = false
responder = async (path, opts) => {
  if (path.endsWith('/scan')) { assert.deepEqual(opts.body, { refresh: false }); await scan; hydrated = true; return { ok: true } }
  return hydrated ? loaded : fresh
}
s.calls.rest.length = 0
const request = q.queryFn()
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(s.calls.rest.filter(([p]) => p.endsWith('/scan')).length, 1, 'opening details automatically loads missing records')
render(undefined); guarded()
release()
const result = await request
assert.deepEqual(result.updates, loaded.updates)
assert.ok(s.calls.invalidations.some(v => v.queryKey.join('/') === 'wp-manager/local|default/sites'))
q = render(result)
assert.match(text(), /Plugin A/); assert.match(text(), /Theme 3/)
assert.match(text(), /Not checked yet/)
assert.equal(button('Update all').props.disabled, false)
assert.ok(s.calls.nodes.some(n => n.type.name === 'Pending' && n.props.pending.total === 4))
// Polling cached details must never trigger another remote scan.
s.calls.rest.length = 0
responder = async () => loaded
await q.queryFn()
assert.equal(s.calls.rest.length, 1)
// Empty success is different from missing data, even when the summary count was zero.
responder = async path => path.endsWith('/scan') ? { ok: true } : (hydrated ? { ...loaded, updates: empty, pending: { total: 0 } } : fresh)
render({ ...loaded, updates: empty, pending: { total: 0 } })
assert.match(text(), /Everything is up to date/)
assert.equal(button('Update all').props.disabled, true)
// A failed initial scan must reject (the scan endpoint uses HTTP 200 for per-site failures).
q = render(undefined)
responder = async path => path.endsWith('/scan') ? { ok: false, error: 'Connection refused' } : fresh
await assert.rejects(q.queryFn(), /Connection refused/)
q = render(undefined, new Error('Connection refused'), { plugins: { 'a/a.php': true } })
guarded(); assert.match(text(), /Connection refused/)
assert.ok(button('Retry'), 'failed loads offer retry without requiring Rescan')
assert.equal(q.refetchInterval({ state: { status: 'error' } }), false, 'do not repeatedly scan a failed site')
// Retained stale data on a query error must not enable changes or claim all-clear.
render({ ...loaded, updates: empty }, new Error('offline'), { plugins: { 'a/a.php': true } }); guarded()
render({ ...loaded, updates: empty, last_error: { message: 'Last scan failed' } })
guarded(); assert.match(text(), /Last scan failed/)
// No-count-but-unscanned sites still get hydrated, and malformed success cannot become all-clear.
q = render(undefined)
let reads = 0
responder = async path => path.endsWith('/scan') ? { ok: true } : (++reads === 1 ? { ...fresh, pending: { total: 0 } } : { ...loaded, updates: undefined })
await assert.rejects(q.queryFn(), /pending update/i)
// Explicit Rescan remains a forced refresh and surfaces ok:false rather than silently succeeding.
render(loaded)
const rescan = s.calls.mutations[1] // fleet scan precedes the detail scan
responder = async (path, opts) => { assert.equal(opts.body.refresh, true); return { ok: false, error: 'Refresh failed' } }
await assert.rejects(rescan.mutationFn(), /Refresh failed/)
// Empty confirmation must never emit a success toast or make a WordPress update request.
render(loaded)
s.calls.rest.length = s.calls.notify.length = 0
const confirm = s.calls.nodes.find(n => n.type.name === 'ConfirmDialog')
await assert.rejects(Promise.resolve().then(() => confirm.props.onConfirm()), /nothing|pending|ready/i)
assert.equal(s.calls.rest.length, 0); assert.equal(s.calls.notify.length, 0)
// A stale confirmation after the item list changes must also fail closed.
s.state.overrides.SiteDialog[1] = { batches: [['plugin', ['removed/removed.php']]] }
s.calls.nodes.length = 0
s.render(route.render()); renders++
await assert.rejects(Promise.resolve().then(() => s.calls.nodes.find(n => n.type.name === 'ConfirmDialog').props.onConfirm()), /pending|ready/i)
assert.equal(s.calls.rest.length, 0)
console.log(`OK ${renders} renders`)
