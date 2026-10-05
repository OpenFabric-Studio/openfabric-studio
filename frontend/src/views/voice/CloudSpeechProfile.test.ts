// @vitest-environment happy-dom
import {afterEach,expect,it,vi} from 'vitest'
import {createApp,h,nextTick,type App} from 'vue'
import {createI18n} from 'vue-i18n'
import CloudSpeechProfile from './CloudSpeechProfile.vue'
import * as providerApi from '../../api/openrouter'
import * as cloudApi from '../../api/cloudSpeech'
import en from '../../locales/en'
vi.mock('../../api/openrouter',async original=>({...await original<typeof import('../../api/openrouter')>(),getProviderCatalog:vi.fn()}))
vi.mock('../../api/cloudSpeech',async original=>({...await original<typeof import('../../api/cloudSpeech')>(),createCloudSpeechProfile:vi.fn()}))
let app:App|undefined
afterEach(()=>{app?.unmount();document.body.replaceChildren();vi.resetAllMocks()})
async function settle(){for(let i=0;i<8;i++)await nextTick()}
it('creates a built-in cloud voice without a recording, transcript or clone permission',async()=>{
  vi.mocked(providerApi.getProviderCatalog).mockResolvedValue({models:[{id:'hexgrad/kokoro-82m',name:'Kokoro',kind:'speech',fingerprint:'a'.repeat(64),prices:[{unit:'character',rate_usd:0.000001,source:'live_catalog'}],supported_voices:['af_heart']}],fingerprint:'b'.repeat(64),fetched_at:'now',expires_at:Date.now()/1000+300})
  vi.mocked(cloudApi.createCloudSpeechProfile).mockResolvedValue({id:'c'.repeat(32),name:'Cloud Reader',consent_confirmed:true,reference_audio_path:'',renderer:'openrouter',cloud:{model:'hexgrad/kokoro-82m',voice:'af_heart'},created_at:'now',updated_at:'now'})
  app=createApp({render:()=>h(CloudSpeechProfile,{})}).use(createI18n({legacy:false,locale:'en',messages:{en}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root)
  const details=root.querySelector('details');if(!details)throw new Error('Missing renderer details');details.open=true;details.dispatchEvent(new Event('toggle'));await settle()
  const name=root.querySelector('input');if(!name)throw new Error('Missing cloud profile name');name.value='Cloud Reader';name.dispatchEvent(new Event('input'));await settle()
  expect(root.querySelector('input[type=file]')).toBeNull();expect(root.querySelector('textarea')).toBeNull();expect(root.querySelector('input[type=checkbox]')).toBeNull()
  root.querySelector('form')?.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await settle()
  expect(cloudApi.createCloudSpeechProfile).toHaveBeenCalledWith({name:'Cloud Reader',model:'hexgrad/kokoro-82m',voice:'af_heart'},expect.any(AbortSignal))
})
