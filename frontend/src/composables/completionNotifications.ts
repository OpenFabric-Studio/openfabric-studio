import { reactive } from 'vue'
import { isObject } from '../api/schemaValidation'
import { i18n } from '../i18n'
const KEY = 'remiqora_generation_notifications'
const MAX_IDS = 10000
export const completionNotifications = reactive({ enabled: false, seen: [] as string[], pending: [] as string[], unread: [] as string[], read: [] as string[], status: '' as '' | 'unsupported' | 'denied' | 'unavailable' })
function ids(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > MAX_IDS || !value.every((id: unknown) => typeof id === 'string' && /^[A-Za-z0-9:_-]{1,160}$/.test(id))) return []
  return value.filter((id): id is string => typeof id === 'string')
}
function stored() {
  let value: unknown
  try { value = JSON.parse(localStorage.getItem(KEY) ?? 'null') } catch { value = null }
  const row = isObject(value) ? value : {}
  const seen = ids(row.seen); const receipt = new Set(seen)
  return { enabled: row.enabled === true, seen, pending: ids(row.pending), unread: ids(row.unread).filter(id => receipt.has(id)), read: ids(row.read) }
}
export function reloadCompletionPreferences(): void {
  Object.assign(completionNotifications, stored()); completionNotifications.status = ''
}
function union(first: string[], second: string[]) { return [...new Set([...first, ...second])].slice(-MAX_IDS) }
function mergeStored(): void {
  const latest = stored()
  completionNotifications.enabled = latest.enabled
  completionNotifications.seen = union(latest.seen, completionNotifications.seen)
  const seen = new Set(completionNotifications.seen)
  completionNotifications.read = union(latest.read, completionNotifications.read).filter(id => seen.has(id))
  const read = new Set(completionNotifications.read)
  completionNotifications.pending = union(latest.pending, completionNotifications.pending).filter(id => !seen.has(id))
  completionNotifications.unread = union(latest.unread, completionNotifications.unread).filter(id => seen.has(id) && !read.has(id))
}
async function serialize(work: () => Promise<void>): Promise<void> {
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks
  if (!locks) { await work(); return }
  let started = false
  try { await locks.request('remiqora-completion-notifications', async () => { started = true; await work() }) }
  catch { if (!started) await work() }
}
function write(): void {
  try { localStorage.setItem(KEY, JSON.stringify({ enabled: completionNotifications.enabled, seen: completionNotifications.seen, pending: completionNotifications.pending, unread: completionNotifications.unread, read: completionNotifications.read })) } catch { /* Memory still prevents repeats when device storage is unavailable. */ }
}
function persist(enabled?: boolean): Promise<void> {
  return serialize(async () => { mergeStored(); if (enabled !== undefined) completionNotifications.enabled = enabled; write() })
}
let syncing = false
function onStorage(event: StorageEvent) { if (event.key === KEY || event.key === null) mergeStored() }
export function startCompletionPreferenceSync(): void { if (!syncing) { syncing = true; window.addEventListener('storage', onStorage) } }
export function stopCompletionPreferenceSync(): void { syncing = false; window.removeEventListener('storage', onStorage) }
export async function notificationCapability(): Promise<boolean> {
  const bridge = window.remiqoraNotifications
  if (bridge) {
    try { const response = await bridge.capability(); return isObject(response) && response.supported === true } catch { return false }
  }
  return typeof Notification !== 'undefined'
}
export async function enableCompletionNotifications(enabled: boolean): Promise<boolean> {
  completionNotifications.status = ''
  if (!enabled) { completionNotifications.enabled = false; await persist(false); return false }
  if (!await notificationCapability()) { completionNotifications.enabled = false; completionNotifications.status = 'unsupported'; await persist(false); return false }
  if (!window.remiqoraNotifications) {
    try {
      const permission = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
      if (permission !== 'granted') { completionNotifications.status = 'denied'; completionNotifications.enabled = false; await persist(false); return false }
    } catch { completionNotifications.enabled = false; completionNotifications.status = 'unavailable'; await persist(false); return false }
  }
  completionNotifications.enabled = true; await persist(true); return true
}
export function rememberGenerationJob(id: string): void {
  if (!/^[A-Za-z0-9:_-]{1,160}$/.test(id) || completionNotifications.seen.includes(id) || completionNotifications.pending.includes(id)) return
  completionNotifications.pending = [...completionNotifications.pending, id].slice(-MAX_IDS); void persist()
}
/** Called only after durable audio and the requested voice have completed. */
export async function notifyGenerationComplete(id: string, title: string): Promise<void> {
  if (!/^[A-Za-z0-9:_-]{1,160}$/.test(id)) return
  await serialize(async () => {
    // Re-read the receipt inside the origin-wide lock, before showing anything.
    // Without Web Locks this remains best effort; the browser tag coalesces IDs.
    mergeStored()
    if (completionNotifications.seen.includes(id)) return
    const observed = completionNotifications.pending.includes(id)
    completionNotifications.seen = [...completionNotifications.seen, id].slice(-MAX_IDS)
    completionNotifications.pending = completionNotifications.pending.filter(pending => pending !== id)
    if (observed) completionNotifications.unread = [...completionNotifications.unread, id].slice(-MAX_IDS)
    write()
    if (!observed || !completionNotifications.enabled) return
    const body = title.slice(0, 500); const notificationTitle = i18n.global.t('generationWorkspace.notificationTitle').slice(0, 120)
    try {
      const bridge = window.remiqoraNotifications
      if (bridge) { const response = await bridge.notify({ title: notificationTitle, body }); if (!isObject(response) || response.shown !== true) completionNotifications.status = 'unavailable' }
      else if (typeof Notification !== 'undefined' && Notification.permission === 'granted') new Notification(notificationTitle, { body, tag: id })
      else completionNotifications.status = 'denied'
    } catch { completionNotifications.status = 'unavailable' }
  })
}
export function markGenerationsRead(engine?: 'ace_step' | 'yue2'): void {
  const prefix = engine === 'ace_step' ? 'ace:' : 'yue:'
  completionNotifications.read = union(completionNotifications.read, completionNotifications.unread.filter(id => !engine || id.startsWith(prefix)))
  completionNotifications.unread = engine ? completionNotifications.unread.filter(id => !id.startsWith(prefix)) : []
  void persist()
}
reloadCompletionPreferences()
