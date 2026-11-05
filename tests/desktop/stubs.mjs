// Test-only stand-ins for '@hermes/plugin-sdk', 'react' and 'react/jsx-runtime'.
// A tiny synchronous "renderer" walks the element tree and calls every function component, so a
// missing import, an undefined component or a crash in any render path fails the test.
// Prop shapes are checked against the Desktop UI kit's real prop types where it matters.

export const calls = { notify: [], rest: [], navigate: [] }
export const state = { overrides: {}, current: null, counter: 0, restData: {} }

// ---------------------------------------------------------------- jsx runtime
export function jsx(type, props, key) {
  if (type === undefined || type === null) throw new Error('jsx() got an undefined element type (missing import?)')
  if (props && Object.prototype.hasOwnProperty.call(props, 'key')) throw new Error('key passed inside props')
  return { $$el: true, type, props: props || {}, key }
}
export const jsxs = jsx
export const Fragment = 'fragment'

// ---------------------------------------------------------------- react
export function useState(initial) {
  const comp = state.current
  const idx = state.counter++
  const o = state.overrides[comp]
  const value = o && Object.prototype.hasOwnProperty.call(o, idx) ? o[idx] : (typeof initial === 'function' ? initial() : initial)
  return [value, () => {}]
}
export function useMemo(fn) { return fn() }
export function useEffect() {}
export function useRef(v) { return { current: v } }
export function useCallback(fn) { return fn }
export default { useState, useMemo, useEffect, useRef, useCallback }

// ---------------------------------------------------------------- render
export function render(node, path = []) {
  if (node === null || node === undefined || node === false || node === true) return
  if (Array.isArray(node)) { node.forEach(n => render(n, path)); return }
  if (typeof node !== 'object') return
  if (!node.$$el) throw new Error(`non-element object rendered as child at ${path.join(' > ')}: ${JSON.stringify(node).slice(0, 80)}`)
  const { type, props } = node
  if (typeof type === 'function') {
    const prev = [state.current, state.counter]
    state.current = type.name
    state.counter = 0
    let out
    try {
      out = type(props)
    } finally {
      ;[state.current, state.counter] = prev
    }
    render(out, [...path, type.name])
    return
  }
  render(props.children, [...path, String(type)])
}

// ---------------------------------------------------------------- SDK
const atom = v => ({ get: () => v, subscribe: () => () => {}, listen: () => () => {} })
export const host = {
  state: { profile: atom('default'), connectionId: atom('local'), viewport: atom({ width: 1200, height: 800, narrow: false }), gateway: atom('open') },
  notify: n => { calls.notify.push(n); return 'id' },
  navigate: p => { calls.navigate.push(p) }
}
export const useValue = a => a.get()
export const queryClient = { invalidateQueries: () => Promise.resolve(), removeQueries: () => {}, cancelQueries: () => Promise.resolve() }
export function useQuery(opts) {
  if (!Array.isArray(opts.queryKey)) throw new Error('queryKey must be an array')
  if (opts.refetchInterval !== undefined && opts.refetchInterval < 30000) throw new Error('refetchInterval below 30s')
  const key = opts.queryKey.slice(2).join('/')
  const data = state.restData[key]
  return { data, isPending: data === undefined, isError: false, error: null, refetch: () => {} }
}
export function useMutation(opts) {
  if (typeof opts.mutationFn !== 'function') throw new Error('mutationFn required')
  return { mutate: () => {}, isPending: false, isError: false, error: null }
}
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const captureGatewayFileDownload = () => async () => {}

const pass = name => {
  const f = props => props.children
  Object.defineProperty(f, 'name', { value: name })
  return f
}
const TONES = ['good', 'muted', 'warn', 'bad']
const BADGES = ['default', 'muted', 'success', 'warn', 'destructive', 'outline', 'solid']
const BUTTON_VARIANTS = ['default', 'destructive', 'outline', 'secondary', 'chip', 'ghost', 'link', 'text', 'textStrong', undefined]
const BUTTON_SIZES = ['default', 'xs', 'sm', 'lg', 'icon', 'icon-xs', 'icon-sm', undefined]

export function Button(props) {
  if (!BUTTON_VARIANTS.includes(props.variant)) throw new Error(`Button variant ${props.variant}`)
  if (!BUTTON_SIZES.includes(props.size)) throw new Error(`Button size ${props.size}`)
  return props.children
}
export function Badge(props) {
  if (props.variant !== undefined && !BADGES.includes(props.variant)) throw new Error(`Badge variant ${props.variant}`)
  return props.children
}
export function StatusDot(props) {
  if (!TONES.includes(props.tone)) throw new Error(`StatusDot tone ${props.tone}`)
  return null
}
export function SearchField(props) {
  if (typeof props.onChange !== 'function' || typeof props.placeholder !== 'string') throw new Error('SearchField props')
  return null
}
export function Checkbox(props) {
  if (props.onChange) throw new Error('Checkbox uses onCheckedChange')
  if (typeof props.onCheckedChange !== 'function') throw new Error('Checkbox needs onCheckedChange')
  return null
}
export function ConfirmDialog(props) {
  for (const k of ['onClose', 'onConfirm']) if (typeof props[k] !== 'function') throw new Error(`ConfirmDialog ${k}`)
  if (typeof props.open !== 'boolean') throw new Error('ConfirmDialog open must be boolean')
  return props.open ? [props.title, props.description, props.children] : null
}
export function EmptyState(props) {
  if (typeof props.title !== 'string') throw new Error('EmptyState title')
  return null
}
export function ErrorState(props) {
  if (!props.title) throw new Error('ErrorState title')
  return props.children
}
export function Codicon(props) {
  if (typeof props.name !== 'string') throw new Error('Codicon name')
  return null
}
export const Input = () => null
export const Skeleton = () => null
export const Dialog = pass('Dialog')
export const DialogContent = pass('DialogContent')
export const DialogHeader = pass('DialogHeader')
export const DialogFooter = pass('DialogFooter')
export const DialogTitle = pass('DialogTitle')
export const DialogDescription = pass('DialogDescription')
