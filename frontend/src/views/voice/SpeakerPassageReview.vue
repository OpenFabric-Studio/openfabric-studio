<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {useI18n} from 'vue-i18n'
import * as api from '../../api/speakerReview'
import {workflowAudioUrl} from '../../api/audiobookWorkflow'
import type {SpeakerReferenceCandidate,SpeakerReview,SpeakerReviewCapability} from '../../api/contracts'
import {createPollingLoop} from '../../composables/polling'
import SpeechAudioPreview from './SpeechAudioPreview.vue'
const props=defineProps<{bookId:string;chapterIndex:number;passageId:string;revision:number;renderIdentity:string;active:boolean}>()
const {t}=useI18n()
const expanded=ref(false),loading=ref(false),busy=ref(false),approved=ref(false),threshold=ref<string|number>(''),selectedId=ref(''),error=ref('')
const capability=ref<SpeakerReviewCapability|null>(null),references=ref<SpeakerReferenceCandidate[]>([]),review=ref<SpeakerReview|null>(null)
const selected=computed(()=>references.value.find(item=>item.id===selectedId.value))
const running=computed(()=>review.value?.state==='queued'||review.value?.state==='running')
const thresholdValue=computed(()=>{const value=Number(threshold.value);return String(threshold.value).trim()&&Number.isFinite(value)&&value>=-1&&value<=1?value:null})
const canRun=computed(()=>props.active&&expanded.value&&capability.value?.available&&selected.value&&approved.value&&thresholdValue.value!==null&&!loading.value&&!busy.value&&!running.value)
let alive=true,generation=0,controller:AbortController|undefined
function current(item:SpeakerReview){return item.book_id===props.bookId&&item.chapter_index===props.chapterIndex&&item.passage_id===props.passageId&&item.revision===props.revision&&item.render_identity===props.renderIdentity}
const validation=createPollingLoop(async({signal,isCurrent})=>{
  const target=review.value,token=generation
  if(!target||target.state!=='completed'||!props.active||!expanded.value)return false
  try{const result=await api.getSpeakerReview(target.id,signal);if(!alive||token!==generation||!isCurrent()||result.id!==target.id||!current(result))return false;review.value=result;return result.state==='completed'}
  catch{if(alive&&token===generation&&isCurrent()){review.value=null;error.value=t('speakerReview.failed')}return false}
},10000)
const polling=createPollingLoop(async({signal,isCurrent})=>{
  const target=review.value,token=generation
  if(!target||!props.active||!expanded.value)return false
  try{const result=await api.getSpeakerReview(target.id,signal);if(!alive||token!==generation||!isCurrent()||result.id!==target.id||!current(result))return false;review.value=result;if(result.state==='completed')validation.start(false);return running.value}
  catch{if(alive&&token===generation&&isCurrent())error.value=t('speakerReview.failed');return false}
},2000)
function reset(){generation++;controller?.abort();polling.stop();validation.stop();loading.value=false;busy.value=false;references.value=[];selectedId.value='';approved.value=false;threshold.value='';review.value=null;capability.value=null;error.value='';expanded.value=false}
watch(()=>[props.bookId,props.chapterIndex,props.passageId,props.revision,props.renderIdentity,props.active],reset)
watch(selectedId,()=>{approved.value=false})
async function load(){
  if(!props.active||loading.value)return
  const token=generation,request=new AbortController();controller=request;loading.value=true;error.value=''
  try{
    const [available,choices,rows]=await Promise.all([api.getSpeakerCapability(request.signal),api.listSpeakerReferences(props.bookId,props.chapterIndex,props.passageId,props.revision,props.renderIdentity,request.signal),api.listSpeakerReviews(props.bookId,request.signal)])
    if(!alive||token!==generation||request.signal.aborted||!expanded.value)return
    capability.value=available;references.value=choices
    review.value=rows.filter(current).sort((a,b)=>b.created_at.localeCompare(a.created_at))[0]??null
    if(running.value)polling.start(false)
    else if(review.value?.state==='completed')validation.start(false)
  }catch{if(alive&&token===generation&&!request.signal.aborted)error.value=t('speakerReview.failed')}
  finally{if(alive&&token===generation&&!request.signal.aborted)loading.value=false}
}
function toggle(event:Event){if(!(event.target instanceof HTMLDetailsElement))return;expanded.value=event.target.open;if(expanded.value)void load();else{generation++;controller?.abort();polling.stop();validation.stop();loading.value=false;busy.value=false}}
async function run(cancel=false){
  if(cancel?busy.value||!review.value:!canRun.value)return
  const token=generation,request=new AbortController();controller?.abort();controller=request;busy.value=true;error.value='';polling.stop();validation.stop()
  try{
    const result=cancel&&review.value?await api.cancelSpeakerReview(review.value.id,request.signal):selected.value&&thresholdValue.value!==null?await api.startSpeakerReview(props.bookId,props.chapterIndex,props.passageId,{revision:props.revision,render_identity:props.renderIdentity,reference_id:selected.value.id,reference_reviewed:true,threshold:thresholdValue.value},request.signal):null
    if(!alive||token!==generation||request.signal.aborted||!result||!current(result))return
    review.value=result;approved.value=false
    if(running.value)polling.start(false)
    else if(result.state==='completed')validation.start(false)
  }catch{if(alive&&token===generation&&!request.signal.aborted)error.value=t('speakerReview.failed')}
  finally{if(alive&&token===generation&&!request.signal.aborted)busy.value=false}
}
onBeforeUnmount(()=>{alive=false;generation++;controller?.abort();polling.stop();validation.stop()})
</script>
<template>
  <details :open="expanded" class="space-y-3 rounded-lg border border-border p-3" @toggle="toggle">
    <summary class="cursor-pointer text-sm font-medium text-text">{{t('speakerReview.title')}}</summary>
    <div v-if="expanded" class="space-y-3 pt-3">
      <p class="text-xs text-text-dim">{{t('speakerReview.intro')}}</p>
      <p class="text-xs text-text-dim">{{t('speakerReview.noNetwork')}}</p>
      <p v-if="loading" role="status" class="text-xs text-text-dim">{{t('common.loading')}}</p>
      <p v-if="capability&&!capability.available" class="text-xs text-status-queued">{{t('speakerReview.unavailable')}}</p>
      <template v-if="capability?.available">
        <label v-if="references.length" class="block space-y-1"><span class="text-xs text-text-dim">{{t('speakerReview.reference')}}</span><select v-model="selectedId" :disabled="busy||running" :aria-label="t('speakerReview.reference')" class="min-h-11 w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"><option value="">{{t('speakerReview.choose')}}</option><option v-for="item in references" :key="item.id" :value="item.id">{{item.label}} · {{item.renderer}} · {{(item.duration_ms/1000).toFixed(1)}}s</option></select></label>
        <p v-else-if="!loading" class="text-xs text-text-dim">{{t('speakerReview.empty')}}</p>
        <SpeechAudioPreview v-if="selected&&workflowAudioUrl(selected.audio_url)" :src="workflowAudioUrl(selected.audio_url)??''" :active="active&&expanded" :label="t('speakerReview.listen')" />
        <label v-if="selected" class="flex gap-2 text-xs text-text"><input v-model="approved" type="checkbox" :disabled="busy||running" :aria-label="t('speakerReview.approval')"><span>{{t('speakerReview.approval')}}</span></label>
        <label class="block space-y-1"><span class="text-xs text-text-dim">{{t('speakerReview.threshold')}}</span><input v-model="threshold" type="number" min="-1" max="1" step="0.01" :disabled="busy||running" :aria-label="t('speakerReview.threshold')" class="min-h-11 w-32 rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"></label>
        <p class="text-xs text-text-dim">{{t('speakerReview.thresholdHint')}}</p>
        <button type="button" :disabled="!canRun" class="min-h-11 rounded-lg border border-border px-3 text-xs text-text disabled:opacity-50" @click="run()">{{t('speakerReview.run')}}</button>
      </template>
      <button v-if="running" type="button" :disabled="busy" class="min-h-11 px-3 text-xs text-text-dim" @click="run(true)">{{t('speakerReview.cancel')}}</button>
      <p v-if="running" role="status" class="text-xs text-text-dim">{{t('speakerReview.pending')}}</p>
      <p v-if="error" role="alert" class="text-xs text-status-failed">{{error}}</p>
      <template v-if="review&&!running">
        <p v-if="review.state!=='completed'" role="status" class="text-xs text-text-dim">{{t(`speakerReview.states.${review.state}`)}}</p>
        <template v-else>
          <p class="text-xs text-text-dim">{{t('speakerReview.resultReference',{reference:review.reference.label})}}</p>
          <p class="text-xs text-text">{{t('speakerReview.score',{score:review.score?.toFixed(3),threshold:review.threshold})}}</p>
          <p class="text-xs text-status-queued">{{t(review.below_threshold?'speakerReview.below':'speakerReview.above')}}</p>
        </template>
        <p v-for="warning in review.warnings" :key="warning" class="text-xs text-text-dim">{{t(`speakerReview.${warning}`)}}</p>
      </template>
    </div>
  </details>
</template>
