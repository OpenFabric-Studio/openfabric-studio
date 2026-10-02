<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { STYLE_TAG_SUGGESTIONS } from '../../composables/styleTags'

const { t } = useI18n()

const props = defineProps<{ modelValue: string; placeholder?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const draft = ref('')
const focused = ref(false)
const composing = ref(false)
const suggestionsOpen = ref(false)
const highlighted = ref(-1)
const inputEl = ref<HTMLInputElement | null>(null)

const tags = computed(() => props.modelValue.split(',').map((t) => t.trim()).filter(Boolean))

function setTags(next: string[]): void {
  emit('update:modelValue', next.join(', '))
}

function addTags(raw: string[]): void {
  const next = [...tags.value]
  const existing = new Set(next.map((t) => t.toLowerCase()))
  for (const item of raw) {
    const tag = item.trim()
    if (!tag || existing.has(tag.toLowerCase())) continue
    next.push(tag)
    existing.add(tag.toLowerCase())
  }
  setTags(next)
}

function addTag(raw: string): void {
  draft.value = ''
  addTags([raw])
}

function removeTag(tag: string): void {
  setTags(tags.value.filter((t) => t !== tag))
}

function clearAll(): void {
  setTags([])
  draft.value = ''
  inputEl.value?.focus()
}

// Pasting a comma-separated list (e.g. copied straight from a tag list
// elsewhere) should split into separate tags immediately, not sit as one
// uncommitted blob until the user manually types a comma/Enter afterwards.
function onPaste(e: ClipboardEvent): void {
  const text = e.clipboardData?.getData('text') ?? ''
  if (!text.includes(',')) return // single value - let it land in the draft as usual
  e.preventDefault()
  draft.value = ''
  addTags(text.split(','))
}

const suggestions = computed(() => {
  const q = draft.value.trim().toLowerCase()
  if (!q) return []
  const existing = new Set(tags.value.map((t) => t.toLowerCase()))
  return STYLE_TAG_SUGGESTIONS.filter((s) => s.toLowerCase().includes(q) && !existing.has(s.toLowerCase())).slice(0, 8)
})

function onInput(): void {
  suggestionsOpen.value = true
  highlighted.value = -1
}

function commitDraft(): void {
  if (!draft.value.trim()) return
  const raw = draft.value
  draft.value = ''
  addTags(raw.split(','))
}

function onBlur(): void {
  focused.value = false
  suggestionsOpen.value = false
  highlighted.value = -1
  if (!composing.value) commitDraft()
}

function onCompositionEnd(e: CompositionEvent): void {
  composing.value = false
  if (e.target instanceof HTMLInputElement) draft.value = e.target.value
  if (!focused.value) commitDraft()
}

function onKeydown(e: KeyboardEvent): void {
  if (composing.value || e.isComposing || e.keyCode === 229) return
  if (e.key === 'ArrowDown' && suggestions.value.length) {
    e.preventDefault()
    suggestionsOpen.value = true
    highlighted.value = (highlighted.value + 1) % suggestions.value.length
  } else if (e.key === 'ArrowUp' && suggestions.value.length) {
    e.preventDefault()
    suggestionsOpen.value = true
    highlighted.value = (highlighted.value - 1 + suggestions.value.length) % suggestions.value.length
  } else if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    const pick = highlighted.value >= 0 ? suggestions.value[highlighted.value] : draft.value
    addTag(pick)
    highlighted.value = -1
  } else if (e.key === 'Escape') {
    if (suggestionsOpen.value) {
      e.stopPropagation()
      suggestionsOpen.value = false
      highlighted.value = -1
    }
  } else if (e.key === 'Backspace' && !draft.value && tags.value.length) {
    removeTag(tags.value[tags.value.length - 1])
  }
}

function selectSuggestion(s: string): void {
  addTag(s)
  highlighted.value = -1
  inputEl.value?.focus()
}
</script>

<template>
  <div class="relative">
    <div class="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-panel-2 p-2">
      <span v-for="tag in tags" :key="tag" class="flex items-center gap-1 rounded-full bg-panel px-2 py-0.5 text-xs text-text">
        {{ tag }}
        <button type="button" class="text-text-dim hover:text-status-failed" @click="removeTag(tag)">✕</button>
      </span>
      <input
        ref="inputEl"
        v-model="draft"
        type="text"
        class="min-w-[8rem] flex-1 bg-transparent text-sm text-text placeholder:text-text-dim focus:outline-none"
        :placeholder="tags.length ? '' : placeholder"
        @input="onInput"
        @keydown="onKeydown"
        @paste="onPaste"
        @focus="focused = true; suggestionsOpen = true"
        @blur="onBlur"
        @compositionstart="composing = true"
        @compositionend="onCompositionEnd"
      />
      <button
        v-if="tags.length"
        type="button"
        class="shrink-0 text-xs text-text-dim hover:text-status-failed"
        :title="t('tagInput.clearAllTitle')"
        @mousedown.prevent="clearAll"
      >
        {{ t('tagInput.clearAll') }}
      </button>
    </div>
    <div v-if="focused && suggestionsOpen && suggestions.length" class="absolute z-10 mt-1 w-full rounded-lg border border-border bg-panel shadow-lg">
      <button
        v-for="(s, i) in suggestions"
        :key="s"
        type="button"
        class="block w-full px-2.5 py-1.5 text-left text-xs"
        :class="i === highlighted ? 'bg-panel-2 text-text' : 'text-text hover:bg-panel-2'"
        @mousedown.prevent="selectSuggestion(s)"
      >
        {{ s }}
      </button>
    </div>
  </div>
</template>
