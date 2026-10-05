<script setup lang="ts">
import { inject, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, routerKey } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { CloudSpeechApproval, CloudSpeechQuote } from '../../api/contracts'
const props = withDefaults(defineProps<{ enabled: boolean; inputKey: string; load: (signal: AbortSignal) => Promise<CloudSpeechQuote>; disabled?: boolean; active?: boolean }>(),{disabled:false,active:true})
const emit=defineEmits<{approval:[value:CloudSpeechApproval|null]}>()
const {t}=useI18n()
const router=inject(routerKey,undefined)
const quote=ref<CloudSpeechQuote|null>(null), checked=ref(false), busy=ref(false), error=ref('')
let alive=true,generation=0,controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined
function clear(){generation++;controller?.abort();controller=undefined;if(timer)clearTimeout(timer);timer=undefined;quote.value=null;checked.value=false;busy.value=false;error.value='';emit('approval',null)}
watch(()=>[props.inputKey,props.enabled,props.active],clear,{immediate:true})
watch(checked,value=>emit('approval',value&&quote.value&&quote.value.expires_at>Date.now()/1000?{quote_id:quote.value.id,transfers_confirmed:true}:null))
async function estimate(){
  if(!props.enabled||props.disabled||!props.active||busy.value)return
  clear();const token=generation,request=new AbortController();controller=request;busy.value=true
  try{
    const result=await props.load(request.signal)
    if(!alive||request.signal.aborted||token!==generation)return
    quote.value=result
    timer=setTimeout(()=>{if(alive&&token===generation){checked.value=false;emit('approval',null);error.value=t('cloudSpeech.expired')}},Math.max(0,result.expires_at*1000-Date.now()))
  }catch{if(alive&&token===generation&&!request.signal.aborted)error.value=t('cloudSpeech.quoteFailed')}
  finally{if(alive&&token===generation&&!request.signal.aborted){busy.value=false;controller=undefined}}
}
onBeforeUnmount(()=>{alive=false;clear()})
</script>
<template>
  <section v-if="enabled" class="space-y-3 rounded-lg border border-accent1/40 bg-accent1/5 p-3" :aria-label="t('cloudSpeech.title')">
    <p class="text-xs text-text-dim">{{t('cloudSpeech.estimateHelp')}}</p>
    <button type="button" :disabled="disabled||busy||!active" class="min-h-11 rounded-lg border border-border px-3 text-sm text-text disabled:opacity-50" @click="estimate">{{t(busy?'cloudSpeech.estimating':'cloudSpeech.estimate')}}</button>
    <template v-if="quote">
      <p role="status" class="text-sm text-text">{{t('cloudSpeech.estimated',{cost:quote.estimated_usd.toFixed(4),count:quote.request_count})}}</p>
      <p class="text-xs text-text-dim">{{t('cloudSpeech.models',{models:(quote.models??[]).join(', ')})}}</p>
      <p class="text-xs text-text-dim">{{t(!quote.request_count?'cloudSpeech.localOnly':quote.transfers?.includes('reference_audio')?'cloudSpeech.transferReference':'cloudSpeech.transferText')}}</p>
      <label class="flex gap-2 text-sm text-text"><input v-model="checked" type="checkbox" :disabled="disabled||!active||quote.expires_at<=Date.now()/1000" :aria-label="t('cloudSpeech.approve')"><span>{{t('cloudSpeech.approve')}}</span></label>
    </template>
    <p v-if="error" role="alert" class="text-sm text-status-failed">{{error}}</p>
    <RouterLink v-if="router" to="/settings#providers" class="inline-block min-h-11 py-3 text-xs text-accent1 underline">{{t('cloudSpeech.settings')}}</RouterLink>
    <a v-else href="/settings#providers" class="inline-block min-h-11 py-3 text-xs text-accent1 underline">{{t('cloudSpeech.settings')}}</a>
  </section>
</template>
