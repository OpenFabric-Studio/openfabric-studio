<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { applyReferenceText } from '../../composables/generationDrafts'
import HelpModal from './HelpModal.vue'
import ReferencePreparationPanel from './ReferencePreparationPanel.vue'
const props = defineProps<{ engine?: 'ace_step' | 'yue2'; melodyDefault?: boolean }>()
const { t } = useI18n(); const router = useRouter(); const open = ref(false); const target = ref<'ace_step' | 'yue2'>(props.engine ?? 'yue2')
function apply(kind: 'lyrics' | 'abc', text: string, importId: string | null) {
  applyReferenceText(props.engine ?? target.value, kind, text, importId)
  open.value = false
  if (!props.engine) void router.push(target.value === 'ace_step' ? '/ace-step' : '/yue2')
}
</script>
<template>
  <button type="button" class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-panel-2" @click="open = true">{{ t('referenceWorkspace.open') }}</button>
  <HelpModal :open="open" :title="t('referenceWorkspace.title')" @close="open = false">
    <label v-if="!engine" class="block text-xs">{{ t('generationWorkspace.engine') }}<select v-model="target" class="ml-2 rounded border border-border bg-panel-2 p-2 text-text"><option value="ace_step">ACE-Step</option><option value="yue2">YuE 2</option></select></label>
    <ReferencePreparationPanel v-if="open" :key="engine ?? target" :engine="engine ?? target" :melody-default="melodyDefault" @apply-lyrics="(text, id) => apply('lyrics', text, id)" @apply-abc="(text, id) => apply('abc', text, id)" />
  </HelpModal>
</template>
