// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import JobCard from '../../views/ace-step/JobCard.vue'
import TrackCard from '../../views/yue2/TrackCard.vue'
import type { AceJob } from '../../stores/aceStep'
import type { Yue2Job } from '../../stores/yue2'
import { listTracks, type SavedTrack } from '../../api/tracks'
import { i18n, setLocale } from '../../i18n'
import type { VoiceJobProgress } from '../../api/contracts'
const conversionProgress: VoiceJobProgress = { job_id: 'apply-42', kind: 'apply', status: 'queued', queued_at: 100, observed_at: 200, phase: 'waiting_gpu', queue_reason: 'voice_training', queue_label: 'SackJo22' }
vi.mock('../../stores/aceStep',async original=>({ ...await original<typeof import('../../stores/aceStep')>(), useAceStepStore:()=>({}) }))
vi.mock('../../stores/yue2',async original=>({ ...await original<typeof import('../../stores/yue2')>(), useYue2Store:()=>({}) }))
vi.mock('../../api/tracks',async original=>({...await original<typeof import('../../api/tracks')>(),listTracks:vi.fn()}))
vi.mock('./StemsPanel.vue',()=>({default:{render:()=>null}}))
vi.mock('./MidiPanel.vue',()=>({default:{render:()=>null}}))
vi.mock('../../composables/audioPlayback',async original=>({...await original<typeof import('../../composables/audioPlayback')>(),fetchAndComputePeaks:vi.fn().mockResolvedValue([.2,.7])}))
vi.mock('../../api/audioVersions',()=>({ listAudioVersions:vi.fn().mockImplementation((trackId:number)=>Promise.resolve({track_id:trackId,original_available:true,versions:[{id:(trackId===42?'a':'d').repeat(32),track_id:trackId,kind:'original',status:'done',created_at:'now',audio_url:trackId===42?'/original.wav':'/second-original.wav'},{id:(trackId===42?'b':'e').repeat(32),track_id:trackId,kind:'voice',status:'running',created_at:'now',voice_name:'Singer',job_progress:conversionProgress}]})) }))
vi.mock('../../api/audioExports',()=>({ listAudioExports:vi.fn().mockImplementation((trackId:number)=>Promise.resolve({exports:trackId===42?[{id:'c'.repeat(32),track_id:42,version_id:'a'.repeat(32),format:'mp3',status:'done',created_at:'now',audio_url:'/original.mp3',filename:'original.mp3',settings:{mp3:{mode:'cbr',bitrate_kbps:320}}}]:[]})) }))
vi.mock('../../api/voices',async original=>({...await original<typeof import('../../api/voices')>(),listVoices:vi.fn().mockResolvedValue([])}))
let app:App|undefined
const savedTrack:SavedTrack={id:42,short_id:42,model:'ace_step',title:'Song',filename:'original.wav',created_at:'2026-10-01T10:00:00Z',lyrics:'',seed:7,duration_ms:4000,wall_ms:null,params:{},audio_url:'/original.wav',abc_url:null,stems:null,midi:null,is_favorite:false}
beforeEach(()=>{
  setLocale('en')
  vi.mocked(listTracks).mockResolvedValue([savedTrack,{...savedTrack,id:43,short_id:43,audio_url:'/second-original.wav'}])
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
  vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(function(this:HTMLMediaElement){this.dispatchEvent(new Event('pause'))})
  vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{})
  vi.spyOn(HTMLMediaElement.prototype,'play').mockImplementation(function(this:HTMLMediaElement){this.dispatchEvent(new Event('play'));return Promise.resolve()})
})
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren()})
async function mount(card:'ace'|'yue', batch=false, view: 'cards' | 'list' = 'cards', replacement = false) {
  const common={id:'saved_42',status:'done',createdAt:Date.now(),voiceApply:'running',voiceName:'Singer'}
  const ace:AceJob={...common,status:'done',voiceApply:'running',voiceProgress:conversionProgress,progress:100,shortIds:batch?[42,43]:[42],finalized:true,title:'Song',audioFormat:'mp3',batchSize:batch?2:1,lyrics:'',dbIds:batch?[42,43]:[42],audioUrls:batch?['/batch-a.wav','/batch-b.wav']:[], origin: replacement ? 'upload' : 'ace_step', params: replacement ? { source: 'voice_replacement', source_track_id: 40 } : {} }
  const yue:Yue2Job={...common,status:'done',voiceApply:'running',voiceProgress:conversionProgress,title:'Song',style:'Song',lyrics:'',finalized:true,seed:7,cot:'off',precision:'q8_0',dbId:42,audioUrl:'/latest.wav'}
  app=createApp({render:()=>card==='ace'?h(JobCard,{job:ace,number:'42',view}):h(TrackCard,{job:yue,number:'42',view})}).use(createPinia()).use(i18n)
  app.component('RouterLink',{props:['to'],template:'<a :href="to"><slot /></a>'})
  const node=document.body.appendChild(document.createElement('div'));app.mount(node)
  for(let i=0;i<15;i++)await nextTick()
  return node
}
it.each(['ace','yue'] as const)('%s exposes immutable original playback while its first voice is processing',async card=>{const node=await mount(card);expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.wav');expect(node.textContent).toContain('Audio versions');expect(node.textContent).toContain('Singer')})
it.each(['ace','yue'] as const)('%s shows one authoritative queue display when audio versions are expanded',async card=>{
  const node = await mount(card)
  expect(node.querySelectorAll('[data-voice-apply-progress]')).toHaveLength(1)
  expect(node.textContent).toContain('Waiting for voice training: SackJo22')
})
it.each(['ace','yue'] as const)('%s retains the queue reason on a collapsed list row and avoids duplicate banners when opened',async card=>{
  const node = await mount(card, false, 'list')
  expect(node.textContent).toContain('Waiting for voice training: SackJo22')
  expect(node.querySelectorAll('[data-voice-apply-progress]')).toHaveLength(1)
  const row = [...node.querySelectorAll('button')].find(item => item.textContent?.includes('Show text/params'))
  if (!row) throw new Error('Missing expand row')
  row.click(); for (let i = 0; i < 15; i++) await nextTick()
  expect(node.querySelectorAll('[data-voice-apply-progress]')).toHaveLength(1)
  expect(node.textContent).toContain('Waiting for voice training: SackJo22')
})
it('shows authoritative queue progress for an uploaded voice replacement',async()=>{
  const node = await mount('ace', false, 'cards', true)
  expect(node.querySelectorAll('[data-voice-apply-progress]')).toHaveLength(1)
  expect(node.textContent).toContain('Waiting for voice training: SackJo22')
})
it.each(['ace','yue'] as const)('%s routes an exact format selection through its single main player',async card=>{
  const node=await mount(card)
  expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
  const format=[...node.querySelectorAll<HTMLButtonElement>('[role="group"][aria-label="Playback format"] button')].find(item=>item.textContent?.startsWith('MP3'))
  if(!format)throw new Error('Missing MP3 playback choice')
  format.click()
  for(let i=0;i<15;i++)await nextTick()
  expect(node.querySelectorAll('audio')).toHaveLength(1)
  expect(node.querySelector('audio')?.getAttribute('src')).toBe('/original.mp3')
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce()
  expect(format.getAttribute('aria-pressed')).toBe('true')
})
it('a saved ACE batch stays stopped when switching A/B variants after an exact export takes playback',async()=>{
  const node=await mount('ace',true)
  const batch=node.querySelector('audio[src="/batch-a.wav"]')?.parentElement
  if(!batch)throw new Error('Missing saved batch comparison')
  const batchPlay=batch.querySelector<HTMLButtonElement>('button[aria-label="Play"]')
  if(!batchPlay)throw new Error('Missing batch Play control')
  batchPlay.click();for(let i=0;i<15;i++)await nextTick()
  const source=batch.querySelector('audio')
  if(!source)throw new Error('Missing active batch source')
  source.currentTime=6
  const format=[...node.querySelectorAll<HTMLButtonElement>('[role="group"][aria-label="Playback format"] button')].find(item=>item.textContent?.startsWith('MP3'))
  if(!format)throw new Error('Missing exact MP3 choice')
  format.click();for(let i=0;i<15;i++)await nextTick()
  expect(batchPlay.getAttribute('aria-label')).toBe('Play')
  const count=vi.mocked(HTMLMediaElement.prototype.play).mock.calls.length
  const next=[...batch.querySelectorAll('button')].find(item=>item.textContent?.trim()==='Variant 2')
  if(!next)throw new Error('Missing second A/B variant')
  next.click();for(let i=0;i<15;i++)await nextTick()
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(count)
  const nextAudio=batch.querySelector('audio[src="/batch-b.wav"]')
  if(!(nextAudio instanceof HTMLAudioElement))throw new Error('Missing second A/B source')
  expect(nextAudio.currentTime).toBe(6)
  expect(node.querySelector('audio[src="/original.mp3"]')?.parentElement?.querySelector('button[aria-label="Pause"]')).not.toBeNull()
})
