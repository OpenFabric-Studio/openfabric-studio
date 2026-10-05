import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getProviderStatus } from '../api/openrouter'
import type { OpenRouterStatus } from '../api/generated'
import { createPollingLoop, type PollContext } from '../composables/polling'
export const useProvidersStore = defineStore('providers', () => {
  const status=ref<OpenRouterStatus | null>(null), offline=ref(false)
  let generation=0, request: AbortController | undefined
  const state=computed(()=>offline.value || !status.value ? 'unknown' : !status.value.enabled ? 'disabled' : !status.value.credential_configured ? 'missing_key' : 'configured')
  async function refresh(context?: PollContext){const token=++generation;request?.abort();const controller=new AbortController();request=controller;const abort=()=>controller.abort();context?.signal.addEventListener('abort',abort,{once:true});try{const value=await getProviderStatus(controller.signal);if(token===generation && !controller.signal.aborted && (!context || context.isCurrent())){status.value=value;offline.value=false}}catch{if(token===generation && !controller.signal.aborted)offline.value=true}finally{context?.signal.removeEventListener('abort',abort);if(token===generation)request=undefined}}
  const loop=createPollingLoop(refresh,()=>20000)
  function accept(value: OpenRouterStatus){generation++;request?.abort();request=undefined;status.value=value;offline.value=false}
  function startPolling(){loop.start()}
  function stopPolling(){generation++;request?.abort();request=undefined;loop.stop()}
  return {status,state,offline,refresh,accept,startPolling,stopPolling}
})
