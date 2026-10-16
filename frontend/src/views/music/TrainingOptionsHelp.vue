<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import HelpModal from '../../components/shared/HelpModal.vue'

defineProps<{ engine: 'stable_audio3' | 'yue2' }>()
const { t } = useI18n()
const open = ref(false)
</script>

<template>
  <button type="button" :aria-label="t('musicLora.help.open')" aria-haspopup="dialog" :aria-expanded="open" class="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-text-dim hover:bg-panel-2 hover:text-text focus-visible:outline-2 focus-visible:outline-accent1" @click="open = true">
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></svg>
  </button>
  <Teleport to="body"><HelpModal :open="open" :title="t('musicLora.help.title')" @close="open = false">
    <p>{{ t('musicLora.help.intro') }}</p>
    <dl class="space-y-4">
      <div><dt class="font-semibold text-text">{{ t('musicLora.name') }}</dt><dd>{{ t('musicLora.help.name') }}</dd></div>
      <div v-if="engine === 'stable_audio3'"><dt class="font-semibold text-text">{{ t('musicLora.simple.baseModel') }}</dt><dd>{{ t('musicLora.help.base') }}</dd></div>
      <div><dt class="font-semibold text-text">{{ t('musicLora.help.musicTitle') }}</dt><dd>{{ t('musicLora.help.music') }} {{ t(engine === 'stable_audio3' ? 'musicLora.simple.audioNote' : 'musicLora.help.excerpts') }}</dd></div>
      <div v-if="engine !== 'stable_audio3'"><dt class="font-semibold text-text">{{ t('musicLora.help.lyricsTitle') }}</dt><dd>{{ t('musicLora.help.lyrics') }}</dd></div>
      <div v-if="engine === 'yue2'"><dt class="font-semibold text-text">{{ t('musicLora.engines.yue2') }}</dt><dd>{{ t('musicLora.modelTraining.acoustic') }} {{ t('musicLora.help.pairPhases') }}</dd></div>
      <div><dt class="font-semibold text-text">{{ t('musicLora.steps') }}</dt><dd>{{ t('musicLora.help.steps') }}</dd></div>
      <div><dt class="font-semibold text-text">{{ t('musicLora.rank') }}</dt><dd>{{ t('musicLora.help.rank') }}</dd></div>
      <div v-if="engine !== 'stable_audio3'"><dt class="font-semibold text-text">{{ t('musicLora.modelTraining.seconds') }}</dt><dd>{{ t('musicLora.help.seconds') }}</dd></div>
      <div><dt class="font-semibold text-text">{{ t('musicLora.learningRate') }}</dt><dd>{{ t('musicLora.help.learningRate') }}</dd></div>
      <div><dt class="font-semibold text-text">{{ t('musicLora.seed') }}</dt><dd>{{ t('musicLora.help.seed') }}</dd></div>
      <div><dt class="font-semibold text-text">{{ t('musicLora.help.progressTitle') }}</dt><dd>{{ t('musicLora.help.progress') }} {{ t('musicLora.simple.runNote') }}</dd></div>
      <div v-if="engine !== 'stable_audio3'"><dt class="font-semibold text-text">{{ t('musicLora.modelTraining.nvidia.title') }}</dt><dd>{{ t('musicLora.modelTraining.nvidia.hint') }}</dd></div>
    </dl>
  </HelpModal></Teleport>
</template>
