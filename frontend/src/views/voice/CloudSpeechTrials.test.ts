// @vitest-environment happy-dom
import {afterEach,expect,it,vi} from 'vitest'
import {createApp,h,nextTick,ref,type App} from 'vue'
import {createI18n} from 'vue-i18n'
import CloudSpeechTrials from './CloudSpeechTrials.vue'
import * as api from '../../api/cloudSpeech'
import en from '../../locales/en'
import type {CloudSpeechTrial} from '../../api/contracts'
vi.mock('../../api/cloudSpeech',async original=>({...await original<typeof import('../../api/cloudSpeech')>(),listCloudTrials:vi.fn()}))
let app:App|undefined
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren();vi.resetAllMocks()})
async function settle(){for(let i=0;i<10;i++)await nextTick()}
it('rediscovers a completed paid trial after remount without submitting anything',async()=>{
  const profileId='a'.repeat(32),trialId='b'.repeat(32)
  vi.mocked(api.listCloudTrials).mockResolvedValue([{id:trialId,profile_id:profileId,created_at:'2026-10-05T13:00:00Z',audio_url:`/api/speech-clone/trials/${trialId}/audio`,provenance:{receipt_id:'c'.repeat(32),profile_id:profileId,model:'hexgrad/kokoro-82m',model_fingerprint:'d'.repeat(64),voice:'af_heart'}}])
  function mount(){app=createApp({render:()=>h(CloudSpeechTrials,{profileId,active:true})}).use(createI18n({legacy:false,locale:'en',messages:{en}}));const root=document.body.appendChild(document.createElement('div'));app.mount(root);return root}
  let root=mount();await settle()
  expect(root.querySelector('audio')?.getAttribute('src')).toBe(`/api/speech-clone/trials/${trialId}/audio`)
  app?.unmount();root.remove();root=mount();await settle()
  expect(api.listCloudTrials).toHaveBeenCalledTimes(2)
  expect(root.querySelector('audio')?.getAttribute('src')).toBe(`/api/speech-clone/trials/${trialId}/audio`)
  expect(root.querySelector('a[download]')?.getAttribute('href')).toBe(`/api/speech-clone/trials/${trialId}/audio`)
})
it('ignores late trial history after the selected profile changes',async()=>{
  const firstProfile='a'.repeat(32),secondProfile='e'.repeat(32),trialId='b'.repeat(32)
  let resolveFirst:(trials:CloudSpeechTrial[])=>void=()=>{throw new Error('History was not requested')}
  const pending=new Promise<CloudSpeechTrial[]>(resolve=>{resolveFirst=resolve})
  vi.mocked(api.listCloudTrials).mockReturnValueOnce(pending).mockResolvedValueOnce([])
  const selected=ref(firstProfile)
  app=createApp({render:()=>h(CloudSpeechTrials,{profileId:selected.value})}).use(createI18n({legacy:false,locale:'en',messages:{en}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root);await settle()
  const signal=vi.mocked(api.listCloudTrials).mock.calls[0]?.[1]
  selected.value=secondProfile;await settle()
  expect(signal?.aborted).toBe(true)
  resolveFirst([{id:trialId,profile_id:firstProfile,created_at:'2026-10-05T13:00:00Z',audio_url:`/api/speech-clone/trials/${trialId}/audio`,provenance:{receipt_id:'c'.repeat(32),profile_id:firstProfile,model:'hexgrad/kokoro-82m',model_fingerprint:'d'.repeat(64),voice:'af_heart'}}]);await settle()
  expect(root.querySelector('audio')).toBeNull()
  expect(root.textContent).toContain('No completed cloud trials')
})
