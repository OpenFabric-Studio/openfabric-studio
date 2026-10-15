import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import * as api from '../api/aceStep'
import { ApiError } from '../api/http'
import type { AceLoraStatus } from '../api/generated'
import type { LoraEntry } from './useLoraRegistry'

export function useAceLora(entries: Ref<LoraEntry[]>, available: () => boolean, blocked: () => boolean) {
  const selection = ref('unconfirmed'), scale = ref(1), actual = ref<AceLoraStatus | null>(null)
  const busy = ref(false), error = ref(''), confirmedChoice = ref<string | null>(null), confirmedName = ref<string | null>(null)
  let alive = true, mounted = false, generation = 0, restored = false, requiresChoice = false
  let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined
  const current = (token: number) => alive && token === generation
  const matches = () => confirmedChoice.value === selection.value && (selection.value === 'none'
    ? actual.value?.use_lora === false
    : actual.value?.lora_loaded === true && actual.value.use_lora && actual.value.active_adapter === confirmedName.value && Math.abs(actual.value.lora_scale - scale.value) < 0.000001)
  const ready = computed(() => !busy.value && !error.value && matches())
  const canScale = computed(() => !busy.value && confirmedChoice.value === selection.value && selection.value !== 'none' && actual.value?.use_lora === true && actual.value.active_adapter === confirmedName.value)
  const selectedName = ref<string | null>(null)
  function clearTimer() { if (timer !== undefined) clearTimeout(timer); timer = undefined }
  async function read(token: number): Promise<AceLoraStatus> {
    const status = await api.loraStatus(controller?.signal)
    if (current(token)) actual.value = status
    return status
  }
  async function run(action: (token: number) => Promise<void>): Promise<boolean> {
    if (busy.value || !alive || !available()) return false
    clearTimer(); const token = ++generation; controller?.abort(); controller = new AbortController(); busy.value = true
    try { await action(token); return current(token) }
    catch (cause) { if (current(token)) error.value = cause instanceof ApiError ? cause.message : 'ace_lora_operation_failed'; return false }
    finally { if (alive) busy.value = false }
  }
  function confirm(choice: string, adapterName: string | null) { confirmedChoice.value = choice; confirmedName.value = adapterName; selectedName.value = adapterName; error.value = ''; requiresChoice = false }
  async function check() {
    return run(async token => {
      const status = await read(token)
      if (!current(token)) return
      if (selection.value === 'none' && confirmedChoice.value === null && !requiresChoice) {
        if (!status.use_lora) confirm('none', null)
        else selection.value = 'unconfirmed'
      }
      if (selection.value === 'unconfirmed') {
        if (!status.use_lora && !requiresChoice) { selection.value = 'none'; confirm('none', null) }
        else { if (!restored) scale.value = status.lora_scale; error.value = '' }
      } else if (matches() && (!error.value || ['ace_lora_status_unavailable', 'ace_lora_selection_mismatch'].includes(error.value))) error.value = ''
    })
  }
  async function choose(choice: string) {
    if (busy.value || blocked() || !alive || (choice !== 'none' && choice !== 'current' && !entries.value.some(entry => `path:${entry.path}` === choice))) return
    selection.value = choice; clearTimer()
    selectedName.value = choice === 'none' ? null : choice === 'current' ? actual.value?.active_adapter ?? confirmedName.value : entries.value.find(entry => `path:${entry.path}` === choice)?.name ?? null
    const currentName = actual.value?.active_adapter
    return run(async token => {
      if (choice === 'none') {
        await api.loraUnload(); if (!current(token)) return
        const status = await read(token)
        if (!current(token)) return
        if (status.lora_loaded || status.use_lora) throw new ApiError('ace_lora_selection_mismatch', 409)
        confirm(choice, null); return
      }
      let status: AceLoraStatus, expectedName = currentName
      if (choice === 'current') {
        status = await read(token); if (!current(token)) return
        if (!currentName || !status.lora_loaded || status.active_adapter !== currentName) throw new ApiError('ace_lora_selection_mismatch', 409)
        if (!restored) scale.value = status.lora_scale
      } else {
        const entry = entries.value.find(item => `path:${item.path}` === choice)
        if (!entry) throw new ApiError('ace_lora_path_missing', 400)
        expectedName = entry.name
        const candidates = [entry.path, `${entry.path}/adapter`, `${entry.path}/adapter/adapter`]
        for (const [index, path] of candidates.entries()) {
          try { await api.loraLoad(path, entry.name); break }
          catch (cause) {
            if (!current(token)) return
            if (!(cause instanceof ApiError) || !['ace_lora_path_missing', 'ace_lora_invalid_adapter'].includes(cause.message) || index === candidates.length - 1) throw cause
          }
          if (!current(token)) return
        }
        if (!current(token)) return
        status = await read(token); if (!current(token)) return
      }
      const adapterName = status.active_adapter
      if (!status.lora_loaded || !adapterName || adapterName !== expectedName) throw new ApiError('ace_lora_selection_mismatch', 409)
      if (!status.use_lora) {
        await api.loraToggle(true); if (!current(token)) return
        status = await read(token); if (!current(token)) return
        if (!status.use_lora || status.active_adapter !== adapterName) throw new ApiError('ace_lora_selection_mismatch', 409)
      }
      confirmedChoice.value = choice; confirmedName.value = adapterName
      await api.loraScale(scale.value); if (!current(token)) return
      status = await read(token); if (!current(token)) return
      if (!status.lora_loaded || !status.use_lora || status.active_adapter !== adapterName || Math.abs(status.lora_scale - scale.value) >= 0.000001) throw new ApiError('ace_lora_selection_mismatch', 409)
      confirm(choice, adapterName)
    })
  }
  async function applyScale() {
    if (!canScale.value || blocked()) return false
    const adapterName = confirmedName.value
    return run(async token => {
      const before = await read(token); if (!current(token)) return
      if (!before.lora_loaded || !before.use_lora || before.active_adapter !== adapterName) throw new ApiError('ace_lora_selection_mismatch', 409)
      await api.loraScale(scale.value); if (!current(token)) return
      const after = await read(token); if (!current(token)) return
      if (!after.lora_loaded || !after.use_lora || after.active_adapter !== adapterName || Math.abs(after.lora_scale - scale.value) >= 0.000001) throw new ApiError('ace_lora_selection_mismatch', 409)
      error.value = ''
    })
  }
  function setScale(value: number) {
    if (!canScale.value || blocked() || !Number.isFinite(value) || value < 0 || value > 1) return
    scale.value = value; clearTimer(); timer = setTimeout(() => { timer = undefined; void applyScale() }, 300)
  }
  async function retry() {
    if (blocked()) return
    if (canScale.value) return applyScale()
    if (selection.value === 'unconfirmed') return check()
    return choose(selection.value)
  }
  async function verify(): Promise<boolean> {
    if (!ready.value) return false
    return run(async token => {
      await read(token); if (!current(token)) return
      if (!matches()) throw new ApiError('ace_lora_selection_mismatch', 409)
      error.value = ''
    })
  }
  function reset(value: number, reselect: boolean) {
    generation++; controller?.abort(); clearTimer(); restored = reselect; requiresChoice = reselect
    selection.value = reselect ? 'unconfirmed' : 'none'; scale.value = value; confirmedChoice.value = null; confirmedName.value = null; selectedName.value = null
    if (actual.value?.use_lora === false && !reselect) confirm('none', null)
  }
  onMounted(() => { mounted = true; if (available()) void check() })
  watch(available, enabled => { if (!mounted) return; if (enabled) void check(); else { generation++; controller?.abort(); clearTimer(); confirmedChoice.value = null } })
  onBeforeUnmount(() => { alive = false; generation++; controller?.abort(); clearTimer() })
  return { selection, scale, actual, busy, error, ready, canScale, name: selectedName, choose, setScale, retry, check, verify, reset }
}
