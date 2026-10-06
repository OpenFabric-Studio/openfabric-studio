<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ModuleLicenseDeclaration } from '../../api/generated'
defineProps<{ licenses?: ModuleLicenseDeclaration[] }>()
const { t } = useI18n()
function sourceUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined } catch { return undefined }
}
</script>

<template>
  <section v-if="licenses?.length" class="mt-3 space-y-2 rounded-lg border border-border p-3">
    <h5 class="text-sm font-semibold text-text">{{ t('moduleWorkspace.licenses') }}</h5>
    <p class="text-xs text-text-dim">{{ t('moduleWorkspace.licenseHint') }}</p>
    <ul class="space-y-3"><li v-for="row in licenses" :key="row.component_id" class="text-xs">
      <p class="font-medium text-text">{{ row.name }}</p>
      <p class="mt-1 text-text">{{ t(`moduleWorkspace.licenseScopes.${row.scope}`) }} · {{ row.status === 'declared' ? row.declared_license : t('moduleWorkspace.licenseUnknown') }}</p>
      <p class="mt-1 text-text-dim">{{ row.notes }}</p>
      <p class="mt-1 text-text-dim">{{ t('moduleWorkspace.licenseReviewed', { date: row.reviewed_at }) }}</p>
      <a v-if="sourceUrl(row.source_url)" :href="sourceUrl(row.source_url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-text underline decoration-accent2">{{ t('moduleWorkspace.licenseSource') }}</a>
    </li></ul>
  </section>
</template>
