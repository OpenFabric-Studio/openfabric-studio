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
      <p class="mt-1 flex flex-wrap gap-2 text-text-dim">
        <span class="rounded-full border border-border px-2 py-1" :class="row.restriction === 'noncommercial' || row.restriction === 'conditional' ? 'text-status-queued' : ''">{{ t(`moduleWorkspace.licenseRestrictions.${row.restriction ?? 'unknown'}`) }}</span>
        <span class="rounded-full border border-border px-2 py-1">{{ t(`moduleWorkspace.licenseRedistribution.${row.redistribution ?? 'unknown'}`) }}</span>
        <span class="rounded-full border border-border px-2 py-1">{{ t(`moduleWorkspace.licenseCopyleft.${row.copyleft ?? 'unknown'}`) }}</span>
      </p>
      <p v-if="row.redistribution === 'bank_only'" class="mt-1 text-text-dim">{{ t('moduleWorkspace.licenseWholeBankHint') }}</p>
      <p class="mt-1 text-text-dim">{{ row.notes }}</p>
      <p class="mt-1 text-text-dim">{{ t('moduleWorkspace.licenseReviewed', { date: row.reviewed_at }) }}</p>
      <a v-if="sourceUrl(row.source_url)" :href="sourceUrl(row.source_url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-text underline decoration-accent2">{{ t('moduleWorkspace.licenseSource') }}</a>
      <details v-if="row.evidence?.length || row.identities?.length" class="mt-2 text-text-dim">
        <summary class="min-h-11 cursor-pointer py-3 text-text focus-visible:outline-2 focus-visible:outline-accent1">{{ t('moduleWorkspace.licenseEvidence') }}</summary>
        <ul v-if="row.evidence?.length" class="space-y-3">
          <li v-for="(evidence, index) in row.evidence" :key="index" class="space-y-1">
            <p>{{ t(`moduleWorkspace.licenseEvidenceKinds.${evidence.kind}`) }}</p>
            <p>{{ t('moduleWorkspace.licenseObservation', { date: evidence.observed_at, reviewer: evidence.reviewer }) }}</p>
            <p v-if="evidence.revision" class="break-all">{{ t('moduleWorkspace.licenseRevision', { revision: evidence.revision }) }}</p>
            <p v-if="evidence.sha256" class="break-all">{{ t('moduleWorkspace.licenseDigest', { digest: evidence.sha256 }) }}</p>
            <a v-if="sourceUrl(evidence.source_url)" :href="sourceUrl(evidence.source_url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-text underline decoration-accent2">{{ t('moduleWorkspace.licenseSource') }}</a>
          </li>
        </ul>
        <div v-if="row.identities?.length" class="mt-3 space-y-2">
          <p class="font-medium text-text">{{ t('moduleWorkspace.licenseIdentity') }}</p>
          <ul class="space-y-3"><li v-for="(identity, index) in row.identities" :key="index" class="space-y-1">
            <p v-if="identity.filename" class="break-all">{{ identity.filename }}</p>
            <p v-if="identity.revision" class="break-all">{{ t('moduleWorkspace.licenseRevision', { revision: identity.revision }) }}</p>
            <p v-if="identity.sha256" class="break-all">{{ t('moduleWorkspace.licenseDigest', { digest: identity.sha256 }) }}</p>
            <a v-if="sourceUrl(identity.repository_url)" :href="sourceUrl(identity.repository_url)" target="_blank" rel="noopener noreferrer" class="inline-block min-h-11 py-3 text-text underline decoration-accent2">{{ t('moduleWorkspace.licenseRepository') }}</a>
          </li></ul>
        </div>
      </details>
    </li></ul>
  </section>
</template>
