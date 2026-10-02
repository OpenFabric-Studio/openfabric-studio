// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { completionNotifications, reloadCompletionPreferences, enableCompletionNotifications, rememberGenerationJob, notifyGenerationComplete, markGenerationsRead } from './completionNotifications'
const shown = vi.fn()
class BrowserNotification { static permission: NotificationPermission = 'granted'; static requestPermission = vi.fn<() => Promise<NotificationPermission>>().mockResolvedValue('granted'); constructor(title: string, options?: NotificationOptions) { shown(title, options) } }
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); vi.stubGlobal('Notification', BrowserNotification); BrowserNotification.permission = 'granted'; BrowserNotification.requestPermission.mockResolvedValue('granted'); delete window.openfabricNotifications; reloadCompletionPreferences() })
afterEach(() => { vi.unstubAllGlobals(); delete window.openfabricNotifications })
it('is off by default, records unread completion, and deduplicates a stable job ID across refreshes', async () => {
  rememberGenerationJob('yue:a'); await notifyGenerationComplete('yue:a', 'Jazz'); await notifyGenerationComplete('yue:a', 'Jazz'); reloadCompletionPreferences(); await notifyGenerationComplete('yue:a', 'Jazz')
  expect(shown).not.toHaveBeenCalled(); expect(completionNotifications.unread).toEqual(['yue:a']); markGenerationsRead(); expect(completionNotifications.unread).toEqual([])
})
it('notifies exactly once when enabled and never floods old unobserved history', async () => {
  await enableCompletionNotifications(true); await notifyGenerationComplete('yue:old', 'Old song'); rememberGenerationJob('yue:new'); await notifyGenerationComplete('yue:new', 'New song'); await notifyGenerationComplete('yue:new', 'New song')
  expect(shown).toHaveBeenCalledTimes(1); expect(shown).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: 'New song', tag: 'yue:new' }))
})
it('supports the validated Electron bridge without requiring browser Notification', async () => {
  vi.stubGlobal('Notification', undefined); const notify = vi.fn().mockResolvedValue({ shown: true }); window.openfabricNotifications = { capability: async () => ({ supported: true }), notify }
  expect(await enableCompletionNotifications(true)).toBe(true); rememberGenerationJob('ace:new'); await notifyGenerationComplete('ace:new', 'Music')
  expect(notify).toHaveBeenCalledTimes(1)
})
it('keeps notifications disabled after denial and rejects malformed local preferences', async () => {
  BrowserNotification.permission = 'denied'; expect(await enableCompletionNotifications(true)).toBe(false); expect(completionNotifications.status).toBe('denied')
  localStorage.setItem('openfabric_generation_notifications', JSON.stringify({ enabled: 'true', seen: ['old'], unread: ['unseen'], pending: [8] })); reloadCompletionPreferences(); expect(completionNotifications.enabled).toBe(false); expect(completionNotifications.unread).toEqual([])
})
it('reads completion receipts from another tab before notifying with stale local state', async () => {
  await enableCompletionNotifications(true); rememberGenerationJob('yue:other-tab')
  localStorage.setItem('openfabric_generation_notifications', JSON.stringify({ enabled: true, seen: ['yue:other-tab'], pending: [], unread: ['yue:other-tab'] }))
  await notifyGenerationComplete('yue:other-tab', 'Already notified elsewhere'); expect(shown).not.toHaveBeenCalled()
})
it('serializes notifications from independent module instances through a browser lock', async () => {
  let queued = Promise.resolve()
  const request = vi.fn((_name: string, callback: () => Promise<void>) => { const operation = queued.then(callback); queued = operation.catch(() => {}); return operation })
  vi.stubGlobal('navigator', { locks: { request } })
  await enableCompletionNotifications(true); rememberGenerationJob('yue:concurrent')
  vi.resetModules(); const secondTab = await import('./completionNotifications')
  await Promise.all([notifyGenerationComplete('yue:concurrent', 'Song'), secondTab.notifyGenerationComplete('yue:concurrent', 'Song')])
  expect(shown).toHaveBeenCalledTimes(1); expect(request).toHaveBeenCalledWith('openfabric-completion-notifications', expect.any(Function))
})
it('synchronizes validated unread and read receipts from another tab', async () => {
  const synced = await import('./completionNotifications')
  const { startCompletionPreferenceSync, stopCompletionPreferenceSync } = synced
  synced.reloadCompletionPreferences(); startCompletionPreferenceSync()
  localStorage.setItem('openfabric_generation_notifications', JSON.stringify({ enabled: false, seen: ['yue:external'], unread: ['yue:external'], pending: [] }))
  window.dispatchEvent(new StorageEvent('storage', { key: 'openfabric_generation_notifications' }))
  expect(synced.completionNotifications.unread).toEqual(['yue:external'])
  localStorage.setItem('openfabric_generation_notifications', JSON.stringify({ enabled: false, seen: ['yue:external'], unread: [], pending: [], read: ['yue:external'] }))
  window.dispatchEvent(new StorageEvent('storage', { key: 'openfabric_generation_notifications' }))
  expect(synced.completionNotifications.unread).toEqual([]); stopCompletionPreferenceSync()
})
