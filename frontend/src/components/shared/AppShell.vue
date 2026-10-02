<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { hasOpenDialog, useDialogA11y } from '../../composables/useDialogA11y'
import AppSidebar from './AppSidebar.vue'
import AppHeader from './AppHeader.vue'
import AppFooter from './AppFooter.vue'

const { t } = useI18n(), route = useRoute()
const STORAGE_KEY = 'openfabric_navigation'
function storedCollapse(): boolean { try { return localStorage.getItem(STORAGE_KEY) === 'collapsed' } catch { return false } }
const collapsed = ref(storedCollapse()), mobileOpen = ref(false)
const media = window.matchMedia('(min-width: 768px)')
const desktop = ref(media.matches)
const sidebar = ref<HTMLElement | null>(null), main = ref<HTMLElement | null>(null)
let alive = true, focusGeneration = 0, previousOverflow: string | null = null
function closeDrawer() { ++focusGeneration; mobileOpen.value = false }
function toggle() {
  if (!desktop.value) { mobileOpen.value ? closeDrawer() : mobileOpen.value = true; return }
  collapsed.value = !collapsed.value
  try { localStorage.setItem(STORAGE_KEY, collapsed.value ? 'collapsed' : 'expanded') } catch { /* Navigation still works without preference storage. */ }
}
useDialogA11y(sidebar, () => !desktop.value && mobileOpen.value, closeDrawer)
watch(() => route.fullPath, closeDrawer)
function unlockScroll() { if (previousOverflow !== null) { document.body.style.overflow = previousOverflow; previousOverflow = null } }
watch(() => !desktop.value && mobileOpen.value, open => {
  if (open) { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden' }
  else unlockScroll()
}, { flush: 'sync' })
async function changeViewport() {
  const wasOpen = mobileOpen.value, request = ++focusGeneration
  desktop.value = media.matches; mobileOpen.value = false
  if (!desktop.value || !wasOpen) return
  await nextTick()
  if (alive && request === focusGeneration && desktop.value && !mobileOpen.value && !hasOpenDialog()) sidebar.value?.querySelector<HTMLButtonElement>('[data-navigation-toggle]')?.focus()
}
async function skip(event: MouseEvent) {
  event.preventDefault(); closeDrawer(); const request = ++focusGeneration
  await nextTick(); if (alive && request === focusGeneration && !hasOpenDialog()) main.value?.focus()
}
onMounted(() => media.addEventListener('change', changeViewport))
onBeforeUnmount(() => { alive = false; ++focusGeneration; media.removeEventListener('change', changeViewport); unlockScroll() })
</script>

<template>
  <div class="app-shell" :data-navigation-collapsed="collapsed" :data-mobile-open="mobileOpen">
    <a href="#main-content" class="skip-link" @click="skip">{{ t('appNavigation.skip') }}</a>
    <div v-if="!desktop && mobileOpen" class="fixed inset-0 z-40 bg-bg/70" aria-hidden="true" @click="closeDrawer" />
    <aside ref="sidebar" data-app-sidebar class="app-sidebar" :inert="!desktop && !mobileOpen" :role="!desktop && mobileOpen ? 'dialog' : undefined" :aria-modal="!desktop && mobileOpen ? true : undefined" :aria-label="t('appNavigation.main')" tabindex="-1">
      <AppSidebar :collapsed="desktop && collapsed" :mobile="!desktop" :active="desktop || mobileOpen" @toggle="toggle" @navigate="closeDrawer" />
    </aside>
    <div class="app-content" :inert="!desktop && mobileOpen">
      <AppHeader :mobile="!desktop" :navigation-open="mobileOpen" @toggle-navigation="toggle" />
      <main id="main-content" ref="main" tabindex="-1" class="mx-auto w-full max-w-7xl min-w-0 flex-1 px-4 py-5 outline-none sm:px-6"><slot /></main>
      <AppFooter />
    </div>
  </div>
</template>

<style scoped>
.app-shell { --navigation-width: 224px; min-width: 0; min-height: 100svh; flex: 1; }
.app-shell[data-navigation-collapsed="true"] { --navigation-width: 64px; }
.app-sidebar { position: fixed; inset: 0 auto 0 0; z-index: 50; width: var(--navigation-width); border-right: 1px solid var(--color-border); background: var(--color-panel); outline: none; }
.app-content { display: flex; flex-direction: column; min-width: 0; min-height: 100svh; margin-left: var(--navigation-width); }
.skip-link { position: fixed; top: 8px; left: 8px; z-index: 100; transform: translateY(-150%); padding: 10px 16px; border-radius: 8px; background: var(--color-accent1); color: white; }
.skip-link:focus { transform: none; outline: 2px solid var(--color-text); }
@media (max-width: 767px) {
  .app-sidebar { width: min(272px, calc(100vw - 32px)); visibility: hidden; transform: translateX(-100%); }
  [data-mobile-open="true"] .app-sidebar { visibility: visible; transform: none; }
  .app-content { margin-left: 0; }
}
</style>
