// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { VideoCloudQuoteResponse, VideoProject } from '../../api/contracts'
import * as api from '../../api/videos'
import * as provider from '../../api/openrouter'
import { videoProjectFixture } from './videoFixtures'
import VideoCloudPanel from './VideoCloudPanel.vue'
vi.mock('../../api/videos', async original => ({ ...await original<typeof import('../../api/videos')>(), quoteCloudVideo: vi.fn() }))
vi.mock('../../api/openrouter', () => ({ getProviderCatalog: vi.fn(), getProviderStatus: vi.fn() }))
let app: App | undefined
const shotId='b'.repeat(32)
const quote: VideoCloudQuoteResponse={project_id:'a'.repeat(32),revision:1,shot_id:shotId,remote_duration_sec:4,slot_duration_sec:2,trim_required:true,
  quote:{id:'c'.repeat(32),kind:'video',model_id:'google/veo-3.1-fast',model_fingerprint:'d'.repeat(64),request_fingerprint:'e'.repeat(64),estimated_usd:.4,expires_at:Date.now()/1000+300,transfers:['prompt','reference_image']}}
async function settle() { for (let index=0;index<10;index++) await nextTick() }
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(provider.getProviderCatalog).mockResolvedValue({models:[{id:'google/veo-3.1-fast',name:'Veo',kind:'video',fingerprint:'d'.repeat(64),prices:[{unit:'second',rate_usd:.1,source:'live_catalog'}],supported_durations:[4,6,8],supported_sizes:['1280x720']}],fingerprint:'e'.repeat(64),fetched_at:'today',expires_at:Date.now()/1000+1000})
  vi.mocked(provider.getProviderStatus).mockResolvedValue({enabled:true,credential_configured:true})
  vi.mocked(api.quoteCloudVideo).mockResolvedValue(quote)
})
afterEach(() => { app?.unmount();app=undefined;document.body.replaceChildren() })
async function mount() {
  const project=ref<VideoProject>({...videoProjectFixture('a'.repeat(32)),provider_config:{provider:'openrouter',model_id:'google/veo-3.1-fast',size:'1280x720'},shots:[{id:shotId,start_sec:0,seconds:2,prompt:'A scene'}]})
  const submit=vi.fn()
  app=createApp({render:()=>h(VideoCloudPanel,{project:project.value,shotId,readOnly:false,dirty:false,onSubmit:submit})}).use(createI18n({legacy:false,locale:'en',messages:{en:{}}}))
  app.use(createRouter({history:createMemoryHistory(),routes:[{path:'/',component:{render:()=>h('div')}},{path:'/settings',component:{render:()=>h('div')}}]}));app.mount(document.body.appendChild(document.createElement('div')));await settle()
  return {project,submit}
}
function action(selector: string): HTMLButtonElement { const node=document.querySelector(selector);if(!(node instanceof HTMLButtonElement)) throw new Error(`Missing ${selector}`);return node }
it('requires transfer and explicit native-tail trim confirmations before one-shot submission',async()=>{
  const {submit}=await mount();action('[data-cloud-quote]').click();await settle()
  expect(action('[data-cloud-submit]').disabled).toBe(true)
  const boxes=document.querySelectorAll<HTMLInputElement>('input[type=checkbox]')
  expect(boxes.length).toBe(2)
  for(const box of boxes){box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}))}
  await settle();action('[data-cloud-submit]').click();await settle()
  expect(submit).toHaveBeenCalledExactlyOnceWith('a'.repeat(32),{revision:1,shot_id:shotId,remote_duration_sec:4,quote_id:quote.quote.id,transfers_confirmed:true,trim_confirmed:true})
  expect(document.querySelector('[data-cloud-submit]')).toBeNull()
})
it('aborts and discards a late quote when the same project revision changes',async()=>{
  let release:(value:VideoCloudQuoteResponse)=>void=()=>{throw new Error('Uninitialized')}
  vi.mocked(api.quoteCloudVideo).mockReturnValueOnce(new Promise(resolve=>{release=resolve}))
  const {project,submit}=await mount();action('[data-cloud-quote]').click();await settle()
  const signal=vi.mocked(api.quoteCloudVideo).mock.calls[0]?.[2]
  project.value={...project.value,revision:2};await settle()
  release(quote);await settle()
  expect(signal?.aborted).toBe(true)
  expect(document.querySelector('[data-cloud-submit]')).toBeNull()
  expect(submit).not.toHaveBeenCalled()
})
