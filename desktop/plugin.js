// wp-manager — Hermes Desktop page for managing WordPress sites.
// Plain disk-plugin ESM (no build step, no JSX): React and the UI kit are supplied by Desktop.
// Talks only to this plugin's backend namespace (/api/plugins/wp-manager) through ctx.rest.
import * as sdk from '@hermes/plugin-sdk'
import {
  host,
  useValue,
  useQuery,
  useMutation,
  queryClient,
  Badge,
  Button,
  Checkbox,
  Codicon,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  Input,
  SearchField,
  Skeleton,
  StatusDot,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'wp-manager'
const ROUTE = '/wp-manager'
const POLL_MS = 60000
const DETAIL_POLL_MS = 30000
const UPDATE_TIMEOUT_MS = 30 * 60 * 1000
const SCAN_TIMEOUT_MS = 10 * 60 * 1000
const TYPES = [
  ['plugins', 'plugin', 'Plugins'],
  ['themes', 'theme', 'Themes'],
  ['core', 'core', 'WordPress core'],
  ['translations', 'translations', 'Translations']
]

// h(type, props, ...children): jsx()/jsxs() with the key lifted out of props.
function h(type, props, ...children) {
  const { key, ...rest } = props || {}
  const kids = children.filter(c => c !== undefined)
  if (kids.length === 0) return jsx(type, rest, key)
  if (kids.length === 1) return jsx(type, { ...rest, children: kids[0] }, key)
  return jsxs(type, { ...rest, children: kids }, key)
}

const muted = { color: 'var(--ui-text-tertiary)' }
const faint = { color: 'var(--ui-text-quaternary)' }
const rule = { borderBottom: '1px solid var(--ui-stroke-secondary)' }

function errorText(err) {
  if (!err) return ''
  const detail = err.detail || (err.body && err.body.detail)
  if (detail && typeof detail === 'object' && detail.error) return String(detail.error)
  return String(err.message || err)
}

function ago(iso) {
  if (!iso) return 'never'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return String(iso)
  const s = Math.max(0, Math.round((Date.now() - t) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

function tone(verdict) {
  if (verdict === 'ok') return 'good'
  if (verdict === 'degraded') return 'warn'
  if (verdict === 'down') return 'bad'
  return 'muted'
}

function HealthDot({ health }) {
  const verdict = health && health.verdict
  const title = verdict ? `${verdict}${health.failing && health.failing.length ? ': ' + health.failing.join('; ') : ''}` : 'not checked yet'
  return h('span', { title, className: 'inline-flex items-center gap-1.5' },
    h(StatusDot, { tone: tone(verdict) }),
    h('span', { className: 'text-xs', style: muted }, verdict || '—'))
}

function Pending({ pending }) {
  const p = pending || {}
  return h('span', { className: 'inline-flex flex-wrap gap-1' },
    ...TYPES.map(([key, , label]) => h(Badge, {
      key, variant: p[key] ? 'warn' : 'muted', title: `${label}: ${p[key] || 0} pending`
    }, `${label[0]}${label === 'Translations' ? 'r' : ''} ${p[key] || 0}`)))
}

// ------------------------------------------------------------------ data hooks
function useScope() {
  // connectionId is missing on older Desktop builds; the branch is fixed for the app's lifetime.
  const connection = (host.state.connectionId ? useValue(host.state.connectionId) : null) || 'local'
  const profile = useValue(host.state.profile) || 'default'
  return `${connection}|${profile}`
}

function keyFor(scope, ...rest) {
  return [ID, scope, ...rest]
}

function invalidateAll() {
  return queryClient.invalidateQueries({ queryKey: [ID] })
}

function updatesReady(site) {
  return Boolean(site && site.last_checked && site.updates &&
    TYPES.every(([key]) => Array.isArray(site.updates[key])))
}

// ------------------------------------------------------------------ page
function Page({ api }) {
  const scope = useScope()
  const viewport = useValue(host.state.viewport)
  const narrow = Boolean(viewport && viewport.narrow)
  const [search, setSearch] = useState('')
  const [tag, setTag] = useState('')
  const [openSite, setOpenSite] = useState(null)
  const [adding, setAdding] = useState(false)

  const sites = useQuery({
    queryKey: keyFor(scope, 'sites'),
    queryFn: () => api.rest('/sites'),
    refetchInterval: POLL_MS,
    retry: false
  })
  const scan = useMutation({
    mutationFn: () => api.rest('/scan', { method: 'POST', body: { refresh: true, tag: tag || undefined }, timeoutMs: SCAN_TIMEOUT_MS }),
    onSuccess: res => {
      host.notify({
        kind: res.failed ? 'warning' : 'success',
        message: `Scanned ${res.scanned} site${res.scanned === 1 ? '' : 's'}: ${res.totals.total} update${res.totals.total === 1 ? '' : 's'} pending${res.failed ? `, ${res.failed} failed` : ''}`
      })
      void invalidateAll()
    },
    onError: err => host.notify({ kind: 'error', message: `Scan failed: ${errorText(err)}` })
  })

  const data = sites.data || { sites: [], tags: [] }
  const q = search.trim().toLowerCase()
  const rows = (data.sites || []).filter(s =>
    (!tag || (s.tags || []).includes(tag)) &&
    (!q || [s.label, s.url, s.id, ...(s.tags || [])].some(v => String(v || '').toLowerCase().includes(q))))

  let body
  if (sites.isPending) {
    body = h('div', { role: 'status', className: 'flex flex-col gap-2' },
      h(Skeleton, { className: 'h-8 w-full' }), h(Skeleton, { className: 'h-8 w-full' }))
  } else if (sites.isError) {
    body = h(ErrorState, {
      title: 'WordPress manager unavailable',
      description: `${errorText(sites.error)}. The wp-manager plugin must be installed and enabled (plugins.enabled) on the Hermes you are connected to, and its gateway restarted.`
    }, h(Button, { variant: 'outline', onClick: () => sites.refetch() }, 'Retry'))
  } else if (!(data.sites || []).length) {
    body = h('div', { className: 'flex flex-col items-center gap-3' },
      h(EmptyState, {
        title: 'No WordPress sites yet',
        description: 'Install the connector plugin on a site, open Tools → Hermes Connector, create a connection, then paste the string here.'
      }),
      h(Button, { onClick: () => setAdding(true) }, 'Add site'))
  } else if (!rows.length) {
    body = h(EmptyState, { title: 'No sites match', description: 'Clear the search or tag filter.' })
  } else {
    body = narrow
      ? h(SiteCards, { rows, onOpen: setOpenSite })
      : h(SiteTable, { rows, onOpen: setOpenSite })
  }

  return h('main', { className: 'flex h-full flex-col gap-4 overflow-auto p-5 text-sm' },
    h('header', { className: 'flex flex-wrap items-center gap-3' },
      h(Codicon, { name: 'globe', size: 18 }),
      h('h1', { className: 'text-lg font-semibold' }, 'WordPress'),
      h('span', { className: 'text-xs', style: muted },
        `${(data.sites || []).length} site${(data.sites || []).length === 1 ? '' : 's'}`),
      h('div', { className: 'ml-auto flex flex-wrap gap-2' },
        h(Button, { variant: 'outline', disabled: scan.isPending || !(data.sites || []).length, onClick: () => scan.mutate() },
          scan.isPending ? 'Scanning…' : (tag ? `Scan “${tag}”` : 'Scan all')),
        h(ConnectorDownload, { api }),
        h(Button, { onClick: () => setAdding(true) }, 'Add site'))),
    h('div', { className: 'flex flex-wrap items-center gap-2' },
      h(SearchField, { placeholder: 'Search sites', 'aria-label': 'Search sites', value: search, onChange: setSearch }),
      ...(data.tags || []).length
        ? [h(Button, { key: '*', size: 'sm', variant: tag ? 'ghost' : 'secondary', onClick: () => setTag('') }, 'All'),
            ...(data.tags || []).map(t => h(Button, {
              key: t, size: 'sm', variant: tag === t ? 'secondary' : 'ghost', 'aria-pressed': tag === t, onClick: () => setTag(tag === t ? '' : t)
            }, t))]
        : []),
    h('p', { className: 'text-xs', style: faint },
      'wp-manager takes no backups. Make sure your host has a recent backup before running updates.'),
    body,
    openSite ? h(SiteDialog, { key: openSite, api, scope, siteId: openSite,
      summary: (data.sites || []).find(s => s.id === openSite), onClose: () => setOpenSite(null) }) : undefined,
    adding ? h(AddSiteDialog, { api, onClose: () => setAdding(false) }) : undefined)
}

function SiteTable({ rows, onOpen }) {
  const th = (label, key) => h('th', { key, className: 'px-2 py-1.5 text-left text-xs font-medium', style: { ...muted, ...rule } }, label)
  return h('div', { className: 'overflow-auto' },
    h('table', { className: 'w-full', style: { borderCollapse: 'collapse' } },
      h('thead', null, h('tr', null,
        th('Site', 'site'), th('Tags', 'tags'), th('WordPress', 'wp'), th('PHP', 'php'), th('Connector', 'conn'),
        th('Pending updates', 'pending'), th('Health', 'health'), th('Checked', 'checked'))),
      h('tbody', null, ...rows.map(s => h('tr', {
        key: s.id,
        tabIndex: 0,
        className: 'cursor-pointer',
        style: rule,
        onClick: () => onOpen(s.id),
        onKeyDown: e => { if (e.key === 'Enter') onOpen(s.id) }
      },
      h('td', { className: 'px-2 py-2' },
        h('div', { className: 'font-medium' }, s.label),
        h('div', { className: 'text-xs', style: muted }, s.url, s.insecure ? ' (http — insecure)' : undefined)),
      h('td', { className: 'px-2 py-2' }, h('span', { className: 'inline-flex flex-wrap gap-1' },
        ...(s.tags || []).map(t => h(Badge, { key: t, variant: 'outline' }, t)))),
      h('td', { className: 'px-2 py-2' }, s.wp_version || '—'),
      h('td', { className: 'px-2 py-2' }, s.php_version || '—'),
      h('td', { className: 'px-2 py-2' }, s.connector_version || '—'),
      h('td', { className: 'px-2 py-2' }, h(Pending, { pending: s.pending })),
      h('td', { className: 'px-2 py-2' }, h(HealthDot, { health: s.health })),
      h('td', { className: 'px-2 py-2 text-xs', style: s.last_error ? { color: 'var(--ui-accent)' } : muted, title: s.last_error ? s.last_error.message : undefined },
        s.last_error ? 'error' : ago(s.last_checked)))))))
}

function SiteCards({ rows, onOpen }) {
  return h('div', { className: 'flex flex-col gap-2' }, ...rows.map(s => h('button', {
    key: s.id,
    type: 'button',
    className: 'flex flex-col gap-1.5 rounded-md p-3 text-left',
    style: { border: '1px solid var(--ui-stroke-secondary)' },
    onClick: () => onOpen(s.id)
  },
  h('div', { className: 'flex items-center gap-2' },
    h(HealthDot, { health: s.health }), h('span', { className: 'font-medium' }, s.label)),
  h('div', { className: 'text-xs', style: muted }, s.url),
  h('div', { className: 'text-xs', style: muted },
    `WP ${s.wp_version || '—'} · PHP ${s.php_version || '—'} · connector ${s.connector_version || '—'} · ${ago(s.last_checked)}`),
  h(Pending, { pending: s.pending }))))
}

// ------------------------------------------------------------------ site detail
function SiteDialog({ api, scope, siteId, summary, onClose }) {
  const [selected, setSelected] = useState({})
  const [confirm, setConfirm] = useState(null)
  const [removing, setRemoving] = useState(false)
  const [lastRun, setLastRun] = useState(null)
  const detail = useQuery({
    queryKey: keyFor(scope, 'site', siteId),
    queryFn: async () => {
      const path = `/sites/${encodeURIComponent(siteId)}`
      let site = await api.rest(path)
      if (!updatesReady(site) || site.last_error) {
        // Connection verification has counts, not item records. Load those once on open;
        // refresh:false reads WordPress's cached offers without forcing an update check.
        const result = await api.rest(`${path}/scan`, {
          method: 'POST', body: { refresh: false }, timeoutMs: SCAN_TIMEOUT_MS
        })
        if (!result.ok) throw new Error(result.error || 'Could not load pending updates.')
        site = await api.rest(path)
        if (!updatesReady(site)) throw new Error('The pending update list is unavailable. Please retry.')
        // Both views now read the same cached scan. Do not invalidate this in-flight query.
        void queryClient.invalidateQueries({ queryKey: keyFor(scope, 'sites') })
      }
      return site
    },
    refetchInterval: query => query.state.status === 'error' ? false : DETAIL_POLL_MS,
    retry: false
  })
  const rescan = useMutation({
    mutationFn: async () => {
      const result = await api.rest(`/sites/${encodeURIComponent(siteId)}/scan`, { method: 'POST', body: { refresh: true }, timeoutMs: SCAN_TIMEOUT_MS })
      if (!result.ok) throw new Error(result.error || 'Could not refresh pending updates.')
      return result
    },
    onSettled: () => void invalidateAll(),
    onError: err => host.notify({ kind: 'error', message: errorText(err) })
  })
  const check = useMutation({
    mutationFn: () => api.rest(`/sites/${encodeURIComponent(siteId)}/health`),
    onSettled: () => void invalidateAll(),
    onError: err => host.notify({ kind: 'error', message: errorText(err) })
  })

  const site = detail.data
  const ready = !detail.isError && !rescan.isPending && updatesReady(site) && !site.last_error
  const updates = (site && site.updates) || {}
  const toggle = (type, id) => setSelected(prev => {
    const next = { ...prev, [type]: { ...(prev[type] || {}) } }
    if (next[type][id]) delete next[type][id]
    else next[type][id] = true
    return next
  })
  const chosen = TYPES.map(([key, type]) => [type,
    (updates[key] || []).filter(item => (selected[key] || {})[item.id]).map(item => item.id)]).filter(([, items]) => items.length)
  const allBatches = TYPES.filter(([key]) => (updates[key] || []).length).map(([, type]) => [type, 'all'])

  async function runBatches(batches) {
    if (!ready || !batches.length || batches.some(([type, items]) => {
      const group = TYPES.find(([, t]) => t === type)
      const available = group ? (updates[group[0]] || []).map(item => item.id) : []
      return !available.length || (items !== 'all' && (!items.length || items.some(id => !available.includes(id))))
    })) throw new Error('No pending updates are ready to run. Reload the list before confirming.')
    const results = []
    for (const [type, items] of batches) {
      const res = await api.rest(`/sites/${encodeURIComponent(siteId)}/update`, {
        method: 'POST', body: { type, items }, timeoutMs: UPDATE_TIMEOUT_MS
      })
      results.push(res)
      if (res.alert) break // stop on a failed health check: do not stack more changes on a broken site
    }
    setLastRun(results)
    setSelected({})
    void invalidateAll()
    const alert = results.find(r => r.alert)
    const failed = results.find(r => r.warning)
    if (alert) host.notify({ kind: 'error', message: alert.alert })
    else if (failed) host.notify({ kind: 'warning', message: failed.warning })
    else host.notify({ kind: 'success', message: `Updated ${site.label}; health check ok.` })
  }

  const describe = batches => batches.map(([type, items]) => `${type}: ${items === 'all' ? 'all available' : items.join(', ')}`).join('\n')

  return h(Dialog, { open: true, onOpenChange: v => { if (!v) onClose() } },
    h(DialogContent, { className: 'max-w-3xl' },
      h(DialogHeader, null,
        h(DialogTitle, null, site ? site.label : summary ? summary.label : siteId),
        h(DialogDescription, null, site ? `${site.url} · WordPress ${site.wp_version || '?'} · PHP ${site.php_version || '?'} · last checked ${ago(site.last_checked)}` : 'Loading…')),
      h('div', { className: 'flex max-h-[60vh] flex-col gap-4 overflow-auto text-sm' },
            h(Pending, { pending: (site || summary || {}).pending }),
            detail.isError || (site && site.last_error)
              ? h(ErrorState, { title: 'Could not load pending updates', description: errorText(detail.error || site.last_error) },
                h(Button, { variant: 'outline', onClick: () => detail.refetch() }, 'Retry'))
              : !ready
                ? h('div', { role: 'status', className: 'flex flex-col gap-2' },
                  'Loading pending updates…', h(Skeleton, { className: 'h-24 w-full' }))
                : h(UpdatesPanel, { updates, selected, toggle }),
            site ? h('div', { className: 'flex flex-col gap-4' },
            site.last_error ? h('div', { className: 'text-xs', style: { color: 'var(--ui-accent)' } }, `Last scan failed: ${site.last_error.message}`) : undefined,
            h(HealthPanel, { site, busy: check.isPending, onCheck: () => check.mutate() }),
            lastRun ? h(RunResults, { runs: lastRun }) : undefined,
            h(ActionsPanel, { actions: site.recent_actions || [] })) : undefined),
      h(DialogFooter, null,
        h(Button, { variant: 'ghost', onClick: () => setRemoving(true) }, 'Remove'),
        h(Button, { variant: 'outline', disabled: !site, onClick: () => site && api.os.openExternal(site.admin_url) },
          h(Codicon, { name: 'link-external', size: 12 }), 'Open wp-admin'),
        h(Button, { variant: 'outline', disabled: rescan.isPending || detail.isPending, onClick: () => rescan.mutate() }, rescan.isPending ? 'Scanning…' : 'Rescan'),
        h(Button, { variant: 'outline', disabled: !ready || !chosen.length, onClick: () => setConfirm({ batches: chosen }) }, 'Update selected'),
        h(Button, { disabled: !ready || !allBatches.length, onClick: () => setConfirm({ batches: allBatches }) }, 'Update all'))),
    h(ConfirmDialog, {
      open: Boolean(confirm),
      onClose: () => setConfirm(null),
      onConfirm: () => runBatches(confirm ? confirm.batches : []),
      title: `Update ${site ? site.label : siteId}?`,
      description: confirm ? `${describe(confirm.batches)}\n\nwp-manager takes no backups — confirm your host has a recent one. A health check runs after each batch.` : '',
      confirmLabel: 'Update now',
      busyLabel: 'Updating…'
    }),
    h(ConfirmDialog, {
      open: removing,
      onClose: () => setRemoving(false),
      onConfirm: async () => {
        await api.rest(`/sites/${encodeURIComponent(siteId)}`, { method: 'DELETE' })
        void invalidateAll()
        onClose()
      },
      title: `Remove ${site ? site.label : siteId} from wp-manager?`,
      description: 'The site itself is not changed. Revoke its Application Password in WordPress (Users → Profile) if you no longer need it.',
      confirmLabel: 'Remove',
      destructive: true,
      dismissOnConfirm: true
    }))
}

function Section({ title, aside, children }) {
  return h('section', { className: 'flex flex-col gap-2' },
    h('div', { className: 'flex items-center gap-2' },
      h('h3', { className: 'text-xs font-semibold uppercase', style: muted }, title), aside), children)
}

function HealthPanel({ site, busy, onCheck }) {
  const hd = site.health_detail
  return h(Section, {
    title: 'Health',
    aside: h(Button, { size: 'xs', variant: 'ghost', disabled: busy, onClick: onCheck }, busy ? 'Checking…' : 'Check now')
  },
  hd
    ? h('div', { className: 'flex flex-col gap-1' },
      h(HealthDot, { health: hd }),
      ...(hd.failing || []).map((f, i) => h('div', { key: i, className: 'text-xs' }, `• ${f}`)),
      h('div', { className: 'text-xs', style: faint }, `checked ${ago(hd.checked_at)}`))
    : h('div', { className: 'text-xs', style: muted }, 'Not checked yet.'))
}

function UpdatesPanel({ updates, selected, toggle }) {
  const groups = TYPES.filter(([key]) => (updates[key] || []).length)
  if (!groups.length) return h(Section, { title: 'Pending updates' }, h('div', { className: 'text-xs', style: muted }, 'Everything is up to date (as of the last scan).'))
  return h(Section, { title: 'Pending updates' },
    ...groups.map(([key, , label]) => h('div', { key, className: 'flex flex-col gap-1' },
      h('div', { className: 'text-xs font-medium' }, label),
      ...(updates[key] || []).map(item => h('label', { key: item.id, className: 'flex items-center gap-2' },
        h(Checkbox, { checked: Boolean((selected[key] || {})[item.id]), onCheckedChange: () => toggle(key, item.id) }),
        h('span', null, item.name || item.id),
        h('span', { className: 'text-xs', style: muted },
          `${item.current_version ? item.current_version + ' → ' : ''}${item.new_version || ''}${item.language ? ' (' + item.language + ')' : ''}`),
        item.compatible === false ? h(Badge, { variant: 'destructive' }, 'incompatible') : undefined,
        item.is_major ? h(Badge, { variant: 'warn' }, 'major') : undefined,
        item.allowed === false ? h(Badge, { variant: 'muted' }, 'blocked by connector settings') : undefined)))))
}

function RunResults({ runs }) {
  return h(Section, { title: 'Last update run' },
    ...runs.map((r, i) => h('div', { key: i, className: 'flex flex-col gap-1' },
      r.alert ? h('div', { className: 'text-xs font-semibold', style: { color: 'var(--ui-accent)' } }, r.alert) : undefined,
      r.warning ? h('div', { className: 'text-xs', style: { color: 'var(--ui-accent)' } }, r.warning) : undefined,
      ...(r.results || []).map((x, j) => h('div', { key: j, className: 'text-xs' },
        `${x.success ? '✓' : '✗'} ${x.name || x.item}: ${x.from_version || '?'} → ${x.to_version || '?'}${x.error ? ' — ' + x.error : ''}`)),
      h('div', { className: 'text-xs', style: muted }, `${r.type}: ${r.update_result}; health ${r.health ? r.health.verdict : '?'}`))))
}

function ActionsPanel({ actions }) {
  return h(Section, { title: 'Recent actions' },
    actions.length
      ? h('div', { className: 'flex flex-col gap-0.5' }, ...actions.slice(0, 12).map((a, i) => h('div', { key: i, className: 'text-xs' },
        h('span', { style: faint }, `${ago(a.time)} · ${a.actor} · `),
        `${a.action}${a.type ? ' ' + a.type : ''}${a.items ? ' ' + (Array.isArray(a.items) ? a.items.join(', ') : a.items) : ''} → ${a.result}${a.health ? ' (health ' + a.health + ')' : ''}`)))
      : h('div', { className: 'text-xs', style: muted }, 'No actions yet.'))
}

// ------------------------------------------------------------------ add site
function Field({ label, hint, children }) {
  return h('label', { className: 'flex flex-col gap-1' },
    h('span', { className: 'text-xs font-medium' }, label),
    children,
    hint ? h('span', { className: 'text-xs', style: faint }, hint) : undefined)
}

function AddSiteDialog({ api, onClose }) {
  const [mode, setMode] = useState('string')
  const scope = useScope()
  const [storage, setStorage] = useState('')
  const [vaultId, setVaultId] = useState('')
  const vaults = useQuery({ queryKey: keyFor(scope, 'credential-storage'),
    queryFn: () => api.rest('/credential-storage'), retry: false })
  const [credKind, setCredKind] = useState('env')
  const [form, setForm] = useState({ connection_string: '', url: '', username: '', label: '', tags: '', env: '', op: '', allow_insecure: false })
  const [result, setResult] = useState(null)
  const set = k => e => setForm(f => ({ ...f, [k]: e && e.target ? e.target.value : e }))
  const insecure = /^http:\/\//i.test(mode === 'string' ? '' : form.url.trim()) && !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])([:/]|$)/i.test(form.url.trim())

  const add = useMutation({
    mutationFn: () => {
      const body = { label: form.label.trim() || undefined, tags: form.tags.trim() || undefined }
      if (mode === 'string') {
        body.connection_string = form.connection_string.trim()
        body.storage = storage
        if (storage === 'onepassword') body.vault_id = vaultId
        if (storage === 'existing') body.credential = credKind === 'op' ? { op: form.op.trim() } : { env: form.env.trim() }
      } else {
        Object.assign(body, { url: form.url.trim(), username: form.username.trim(), allow_insecure: form.allow_insecure || undefined })
        if (credKind === 'env' && form.env.trim()) body.credential = { env: form.env.trim() }
        if (credKind === 'op') body.credential = { op: form.op.trim() }
      }
      return api.rest('/sites', { method: 'POST', body, timeoutMs: 120000 })
    },
    onSuccess: res => {
      setResult(res)
      setForm(f => ({ ...f, connection_string: '' }))
      void invalidateAll()
    }
  })

  const canSubmit = mode === 'string'
    ? form.connection_string.trim().startsWith('wpmh1:') && (
      (storage === 'onepassword' && vaults.data?.available && vaults.data.vaults.some(v => v.id === vaultId)) ||
      (storage === 'existing' && (credKind === 'op' ? form.op.trim().startsWith('op://') : form.env.trim())))
    : form.url.trim() && form.username.trim() && (credKind !== 'op' || form.op.trim().startsWith('op://'))

  const modeButton = (value, label) => h(Button, { key: value, size: 'sm', variant: mode === value ? 'secondary' : 'ghost', 'aria-pressed': mode === value, onClick: () => setMode(value) }, label)
  const credButton = (value, label) => h(Button, { key: value, size: 'sm', variant: credKind === value ? 'secondary' : 'ghost', 'aria-pressed': credKind === value, onClick: () => setCredKind(value) }, label)

  return h(Dialog, { open: true, onOpenChange: v => { if (!v) onClose() } },
    h(DialogContent, { className: 'max-w-lg' },
      h(DialogHeader, null,
        h(DialogTitle, null, 'Add a WordPress site'),
        h(DialogDescription, null, 'On the site: install “Website Manager: WP Connector for Hermes”, open Tools → Hermes Connector and click “Create connection for Hermes”.')),
      result
        ? h('div', { className: 'flex flex-col gap-2 text-sm' },
          h('div', { className: 'flex items-center gap-2' }, h(StatusDot, { tone: 'good' }), `Connected ${result.site.label} (WordPress ${result.verified.wp_version || '?'})`),
          h('div', { className: 'text-xs' }, `Password: ${result.credential_storage}`),
          result.note ? h('div', { className: 'text-xs', style: muted }, result.note) : undefined,
          result.warning ? h('div', { className: 'text-xs', style: { color: 'var(--ui-accent)' } }, result.warning) : undefined)
        : h('div', { className: 'flex flex-col gap-3 text-sm' },
          h('div', { className: 'flex gap-1' }, modeButton('string', 'Connection string'), modeButton('manual', 'Enter details')),
          mode === 'string'
            ? h('div', { className: 'flex flex-col gap-3' },
              h(Field, { label: 'Connection string', hint: 'Starts with wpmh1:. Choose where the password is kept below. wp-manager never writes it to a file.' },
                h(Input, { type: 'password', autoComplete: 'off', spellCheck: false, value: form.connection_string, onChange: set('connection_string'), placeholder: 'wpmh1:…' })),
              h(Field, { label: 'Password storage' }, h('select', { value: storage, onChange: e => setStorage(e.target.value), 'aria-label': 'Password storage' },
                h('option', { value: '' }, 'Choose storage…'),
                h('option', { value: 'onepassword', disabled: !vaults.data?.available }, '1Password (recommended when available)'),
                h('option', { value: 'existing' }, 'Existing Hermes secret / 1Password reference'))),
              vaults.isPending ? h('span', { role: 'status' }, 'Checking 1Password vault access…') : undefined,
              vaults.isError || vaults.data?.available === false ? h('div', { className: 'text-xs', style: muted },
                '1Password CLI unavailable. Install and sign in to the op CLI on the Hermes machine (see the README), or use an existing Hermes secret.',
                h(Button, { size: 'sm', variant: 'ghost', onClick: () => vaults.refetch() }, 'Retry vault access')) : undefined,
              storage === 'onepassword' ? h(Field, { label: '1Password vault', hint: 'Creates a new item, verifies its ID-based reference, then authenticates with WordPress. Existing items are never overwritten. If verification fails, remove any newly created item manually.' },
                h('select', { value: vaultId, onChange: e => setVaultId(e.target.value), 'aria-label': '1Password vault' },
                  h('option', { value: '' }, 'Choose a vault…'),
                  ...(vaults.data?.vaults || []).map(v => h('option', { key: v.id, value: v.id }, v.name)))) : undefined,
              storage === 'existing' ? h('div', { className: 'flex flex-col gap-2' },
                h('div', null, credButton('env', 'Hermes secret'), credButton('op', '1Password ref')),
                h(Field, { label: credKind === 'op' ? '1Password reference' : 'Hermes secret name', hint: 'The connection-string password is ignored; the existing reference must authenticate.' },
                  h(Input, { value: credKind === 'op' ? form.op : form.env, onChange: set(credKind === 'op' ? 'op' : 'env'), spellCheck: false }))) : undefined,
              undefined)
            : h('div', { className: 'flex flex-col gap-3' },
              h(Field, { label: 'Site address' }, h(Input, { value: form.url, onChange: set('url'), placeholder: 'https://example.com' })),
              h(Field, { label: 'WordPress username' }, h(Input, { value: form.username, onChange: set('username'), autoComplete: 'off' })),
              h('div', { className: 'flex flex-col gap-1' },
                h('span', { className: 'text-xs font-medium' }, 'Application Password'),
                h('div', { className: 'flex flex-wrap gap-1' }, credButton('env', 'Hermes secret'), credButton('op', '1Password ref'))),
              credKind === 'env'
                ? h(Field, { label: 'Secret name (optional)', hint: 'Default WPM_<SITE_ID>_APP_PASSWORD. Set it first, e.g. hermes secrets onepassword set WPM_MYSITE_APP_PASSWORD op://Vault/Item/password' },
                  h(Input, { value: form.env, onChange: set('env'), placeholder: 'WPM_MYSITE_APP_PASSWORD', spellCheck: false }))
                : h(Field, { label: '1Password reference', hint: 'Read with the op CLI on the Hermes machine.' },
                  h(Input, { value: form.op, onChange: set('op'), placeholder: 'op://Vault/Item/password', spellCheck: false })),
              insecure
                ? h('label', { className: 'flex items-center gap-2 text-xs', style: { color: 'var(--ui-accent)' } },
                  h(Checkbox, { checked: form.allow_insecure, onCheckedChange: v => setForm(f => ({ ...f, allow_insecure: Boolean(v) })) }),
                  'This is plain http://. Allow it anyway (the password crosses the network unencrypted).')
                : undefined),
          h(Field, { label: 'Label (optional)' }, h(Input, { value: form.label, onChange: set('label') })),
          h(Field, { label: 'Tags (optional)', hint: 'Comma separated, e.g. clients, shop' }, h(Input, { value: form.tags, onChange: set('tags') })),
          add.isError ? h('div', { className: 'text-xs', style: { color: 'var(--ui-accent)' } }, errorText(add.error)) : undefined),
      h(DialogFooter, null,
        h(Button, { variant: 'ghost', onClick: onClose }, result ? 'Done' : 'Cancel'),
        result ? undefined : h(Button, { disabled: !canSubmit || add.isPending, onClick: () => add.mutate() }, add.isPending ? 'Connecting…' : 'Connect'))))
}

function ConnectorDownload({ api }) {
  const [busy, setBusy] = useState(false)
  const capture = typeof sdk.captureGatewayFileDownload === 'function' ? sdk.captureGatewayFileDownload : null
  if (!capture) return null
  return h(Button, {
    variant: 'outline',
    disabled: busy,
    title: 'Download the WordPress connector plugin zip (upload it in wp-admin → Plugins → Add New → Upload)',
    onClick: async () => {
      setBusy(true)
      try {
        const save = capture()
        const pkg = await api.rest('/connector-package', { method: 'POST' })
        await save(pkg.path, pkg.filename)
      } catch (err) {
        host.notify({ kind: 'error', message: `Could not download the connector: ${errorText(err)}` })
      } finally {
        setBusy(false)
      }
    }
  }, 'Get connector')
}

// ------------------------------------------------------------------ registration
export default {
  id: ID,
  name: 'WordPress (wp-manager)',
  register(ctx) {
    const api = { rest: (path, opts) => ctx.rest(path, opts), os: ctx.os }

    ctx.onEvent('plugin.wp-manager.sites.updated', () => { void invalidateAll() })
    ctx.onDispose(() => { queryClient.removeQueries({ queryKey: [ID] }) })

    async function scanAll() {
      host.navigate(ROUTE)
      host.notify({ kind: 'info', message: 'Scanning WordPress sites…' })
      try {
        const res = await ctx.rest('/scan', { method: 'POST', body: { refresh: true }, timeoutMs: SCAN_TIMEOUT_MS })
        host.notify({
          kind: res.failed ? 'warning' : 'success',
          message: `Scanned ${res.scanned} site${res.scanned === 1 ? '' : 's'}: ${res.totals.total} update${res.totals.total === 1 ? '' : 's'} pending${res.failed ? `, ${res.failed} failed` : ''}`
        })
      } catch (err) {
        host.notify({ kind: 'error', message: `Scan failed: ${errorText(err)}` })
      } finally {
        void invalidateAll()
      }
    }

    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(Page, { api }) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'WordPress', codicon: 'globe' } },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: { id: 'wp-manager.open', label: 'WordPress: Open sites', keywords: ['wordpress', 'wp', 'sites'], run: () => host.navigate(ROUTE) }
      },
      {
        id: 'scan',
        area: PALETTE_AREA,
        data: { id: 'wp-manager.scan', label: 'WordPress: Scan all sites', keywords: ['wordpress', 'wp', 'updates', 'scan'], run: () => { void scanAll() } }
      }
    ])
  }
}
