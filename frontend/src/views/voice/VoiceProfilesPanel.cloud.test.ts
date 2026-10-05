// @vitest-environment happy-dom
import {afterEach,expect,it,vi} from 'vitest'
import {createApp,h,nextTick,type App} from 'vue'
import {createI18n} from 'vue-i18n'
import VoiceProfilesPanel from './VoiceProfilesPanel.vue'
import * as profileApi from '../../api/voiceProfiles'
import * as cloudApi from '../../api/cloudSpeech'
import en from '../../locales/en'
vi.mock('./LocalEnginePanel.vue',()=>({default:{render:()=>null}}))
vi.mock('./StarterSpeechVoices.vue',()=>({default:{render:()=>null}}))
vi.mock('../../api/voiceProfiles',async original=>({...await original<typeof import('../../api/voiceProfiles')>(),listSpeechVoiceProfiles:vi.fn(),getSpeechCloneEngine:vi.fn(),startSpeechCloneTrial:vi.fn()}))
vi.mock('../../api/cloudSpeech',async original=>({...await original<typeof import('../../api/cloudSpeech')>(),quoteTrial:vi.fn(),listCloudTrials:vi.fn()}))
let app:App|undefined
afterEach(()=>{app?.unmount();document.body.replaceChildren();vi.resetAllMocks()})
async function settle(){for(let i=0;i<12;i++)await nextTick()}
it('disables paid trial until review and consumes the shown approval once',async()=>{
  const profileId='a'.repeat(32),quoteId='b'.repeat(32)
  vi.mocked(profileApi.listSpeechVoiceProfiles).mockResolvedValue([{id:profileId,name:'Cloud Reader',consent_confirmed:true,reference_audio_path:'',renderer:'openrouter',cloud:{model:'hexgrad/kokoro-82m',voice:'af_heart'},created_at:'now',updated_at:'now'}])
  vi.mocked(profileApi.getSpeechCloneEngine).mockResolvedValue({installed:false,mock:true})
  vi.mocked(cloudApi.quoteTrial).mockResolvedValue({id:quoteId,estimated_usd:0.001,request_count:1,models:['hexgrad/kokoro-82m'],transfers:['text'],expires_at:Date.now()/1000+300})
  vi.mocked(cloudApi.listCloudTrials).mockResolvedValue([])
  vi.mocked(profileApi.startSpeechCloneTrial).mockResolvedValue({profile_id:profileId,engine:'openrouter',status:'completed',detail:'cloud_speech_completed',trial_id:'c'.repeat(32),output_path:'/private/trial.wav',cloud_receipt_id:'d'.repeat(32)})
  app=createApp({render:()=>h(VoiceProfilesPanel,{})}).use(createI18n({legacy:false,locale:'en',messages:{en}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root);await settle()
  const text=root.querySelector('textarea[aria-label="Text to speak"]');if(!(text instanceof HTMLTextAreaElement))throw new Error('Missing trial text')
  text.value='Hello from cloud.';text.dispatchEvent(new Event('input'));await settle()
  const submit=[...root.querySelectorAll('button')].find(button=>button.textContent==='Generate speech trial');if(!submit)throw new Error('Missing trial button')
  expect(submit.disabled).toBe(true)
  ;[...root.querySelectorAll('button')].find(button=>button.textContent==='Estimate cloud cost')?.click();await settle()
  const approve=root.querySelector('input[type=checkbox]');if(!(approve instanceof HTMLInputElement))throw new Error('Missing cost/transfer approval')
  approve.checked=true;approve.dispatchEvent(new Event('change'));await settle();expect(submit.disabled).toBe(false)
  root.querySelector('form[aria-label="Speech synthesis"]')?.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await settle()
  expect(profileApi.startSpeechCloneTrial).toHaveBeenCalledTimes(1)
  expect(profileApi.startSpeechCloneTrial).toHaveBeenCalledWith(profileId,'Hello from cloud.',expect.any(AbortSignal),'en',{quote_id:quoteId,transfers_confirmed:true})
  expect(root.querySelector('input[type=checkbox]')).toBeNull()
  expect(submit.disabled).toBe(true)
  expect(root.querySelector('audio')).not.toBeNull()
})
