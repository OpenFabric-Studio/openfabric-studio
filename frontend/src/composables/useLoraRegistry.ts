import { ref, watch } from 'vue'
import { isObject } from '../api/schemaValidation'

export interface LoraEntry {
  name: string
  path: string
}

const STORAGE_KEY = 'aicollector_ace_loras_v1'
// LoRA names are retained as GenerationLabel in backend generation snapshots.
const MAX_NAME_LENGTH = 500

function load(): LoraEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const value: unknown = raw ? JSON.parse(raw) : []
    if (!Array.isArray(value)) return []
    const entries: LoraEntry[] = []
    for (const item of value) {
      if (!isObject(item) || typeof item.name !== 'string' || !item.name.trim() || item.name.length > MAX_NAME_LENGTH
        || typeof item.path !== 'string' || !item.path.trim()) return []
      entries.push({ name: item.name, path: item.path })
    }
    return entries
  } catch {
    return []
  }
}

/** Client-side registry of known LoRA adapter paths (the server has no such listing endpoint). */
export function useLoraRegistry() {
  const loras = ref<LoraEntry[]>(load())

  watch(
    loras,
    (val) => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
      } catch {
        // Storage full/unavailable (private browsing) - registry just won't persist.
      }
    },
    { deep: true },
  )

  function add(name: string, path: string) {
    const cleanName = name.trim()
    const cleanPath = path.trim()
    if (!cleanName || cleanName.length > MAX_NAME_LENGTH || !cleanPath) return
    if (loras.value.some((l) => l.path === cleanPath)) return
    loras.value.push({ name: cleanName, path: cleanPath })
  }

  function remove(path: string) {
    loras.value = loras.value.filter((l) => l.path !== path)
  }

  return { loras, add, remove }
}
