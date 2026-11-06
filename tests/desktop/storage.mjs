// Strict rendering + Add Site payload regressions; no real backend or secrets.
import assert from 'node:assert/strict'
import * as s from './stubs.mjs'
const contributions = []
const api = { registerMany: xs => contributions.push(...xs), register: x => contributions.push(x),
  onEvent: () => () => {}, onDispose: () => {}, os: {},
  rest: async (path, opts) => { s.calls.rest.push([path, opts]); return {} } }
;(await import(process.argv[2])).default.register(api)
const route = contributions.find(c => c.area === 'routes')
const V = 'a'.repeat(26)
const form = { connection_string: 'wpmh1:fixture', label: '', tags: '', url: '', username: '',
  env: 'WPM_TEST_APP_PASSWORD', op: 'op://Private/Test/password', app_password: '', allow_insecure: false }
let renders = 0
function render(storage = '', vault = '', data = { available: true, vaults: [{ id: V, name: 'Test vault' }] }, cred = 'env', result = null) {
  for (const k of ['nodes', 'text', 'mutations', 'queries']) s.calls[k].length = 0
  s.state.restData = { sites: { sites: [], tags: [] }, 'credential-storage': data }
  s.state.overrides = { Page: { 3: true }, AddSiteDialog: { 0: 'string', 1: storage, 2: vault, 3: cred, 4: form, 5: result } }
  s.render(route.render()); renders++
  return s.calls.nodes.find(n => n.type === s.Button && n.props.children === 'Connect')
}
assert.equal(render().props.disabled, true, 'no implicit storage')
assert.equal(render('onepassword').props.disabled, true, 'vault required')
assert.equal(render('onepassword', V).props.disabled, false)
let mut = s.calls.mutations.at(-1)
await mut.mutationFn()
assert.deepEqual(s.calls.rest.at(-1)[1].body, { label: undefined, tags: undefined,
  connection_string: 'wpmh1:fixture', storage: 'onepassword', vault_id: V })
assert.equal(render('onepassword', V, { available: false, vaults: [] }).props.disabled, true)
assert.ok(s.calls.text.some(t => t.includes('1Password unavailable')))
assert.equal(render('env').props.disabled, false)
await s.calls.mutations.at(-1).mutationFn()
assert.equal(s.calls.rest.at(-1)[1].body.storage, 'env')
assert.equal(s.calls.rest.at(-1)[1].body.vault_id, undefined)
for (const cred of ['env', 'op']) {
  assert.equal(render('existing', '', undefined, cred).props.disabled, false)
  await s.calls.mutations.at(-1).mutationFn()
  const body = s.calls.rest.at(-1)[1].body
  assert.equal(body.storage, 'existing')
  assert.deepEqual(body.credential, cred === 'op' ? { op: form.op } : { env: form.env })
}
render('', '', undefined, 'env', { site: { label: 'Test' }, verified: {}, credential_storage: 'stored in 1Password vault Test vault' })
assert.ok(s.calls.text.includes('Password: stored in 1Password vault Test vault'))
console.log(`OK ${renders} renders — explicit storage, vault gating, unavailable state, payloads and actual storage`)
