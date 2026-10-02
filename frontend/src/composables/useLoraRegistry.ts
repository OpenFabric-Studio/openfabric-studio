import { ref, watch } from 'vue'

export interface LoraEntry {
  name: string
  path: string
}

const STORAGE_KEY = 'aicollector_ace_loras_v1'

function load(): LoraEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as LoraEntry[]) : []
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
    if (!cleanName || !cleanPath) return
    if (loras.value.some((l) => l.path === cleanPath)) return
    loras.value.push({ name: cleanName, path: cleanPath })
  }

  function remove(path: string) {
    loras.value = loras.value.filter((l) => l.path !== path)
  }

  return { loras, add, remove }
}
