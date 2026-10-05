// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import * as provider from '../../api/openrouter'
import type { OpenRouterCatalog } from '../../api/contracts'
import { videoCloudEn } from '../../locales/videoCloud'
import type { VideoDraft } from './useVideoWorkspace'
import { videoProjectFixture } from './videoFixtures'
import VideoCloudProvider from './VideoCloudProvider.vue'
vi.mock('../../api/openrouter',()=>({getProviderCatalog:vi.fn(),refreshProviderCatalog:vi.fn()}))
let app:App | undefined
const catalog:OpenRouterCatalog={fingerprint:'a'.repeat(64),expires_at:Date.now()/1000+1000,fetched_at:'today',models:[{id:'google/veo-3.1-fast',name:'Veo',kind:'video',fingerprint:'b'.repeat(64),prices:[{unit:'second',rate_usd:.1,source:'live_catalog'}],supported_durations:[4,6,8],supported_sizes:['1280x720','720x1280']}]}
async function settle(){for(let i=0;i<8;i++)await nextTick()}
beforeEach(()=>{vi.resetAllMocks();vi.mocked(provider.getProviderCatalog).mockResolvedValue(catalog)})
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren()})
async function mount(){const project=videoProjectFixture();const draft=ref<VideoDraft>({name:'Film',mode:'generated',direction:'Direction',character_lock:false,seed:42,settings:{engine_pack:'ltx23',width:704,height:448},provider_config:{provider:'local'},export_settings:{},shots:[],markers:[],overlays:[]})
 app=createApp({render:()=>h(VideoCloudProvider,{modelValue:draft.value,'onUpdate:modelValue':(value:VideoDraft)=>{draft.value=value},project,readOnly:false})}).use(createI18n({legacy:false,locale:'en',messages:{en:{videoCloud:videoCloudEn}}}))
 app.use(createRouter({history:createMemoryHistory(),routes:[{path:'/',component:{render:()=>h('div')}},{path:'/settings',component:{render:()=>h('div')}}]}));app.mount(document.body.appendChild(document.createElement('div')));await settle();return draft}
function button(text:string){const node=[...document.querySelectorAll('button')].find(item=>item.textContent===text);if(!node)throw new Error('Missing button');return node}
it('selects only advertised cloud geometry and preserves local engine settings',async()=>{const draft=await mount();const original={...draft.value.settings};button('OpenRouter cloud').click();await settle();expect(draft.value.provider_config).toEqual({provider:'openrouter',model_id:'google/veo-3.1-fast',size:'1280x720',generate_audio:false});expect(draft.value.settings).toEqual(original);button('Local engines').click();await settle();expect(draft.value.provider_config).toEqual({provider:'local'});expect(draft.value.settings).toEqual(original)})
it('drops a late catalog selection after the user switches back to local',async()=>{let release:(value:OpenRouterCatalog)=>void=()=>{throw new Error('Uninitialized')};vi.mocked(provider.getProviderCatalog).mockReturnValueOnce(new Promise(resolve=>{release=resolve}));const draft=await mount();button('OpenRouter cloud').click();await settle();const signal=vi.mocked(provider.getProviderCatalog).mock.calls[0]?.[0];button('Local engines').click();await settle();release(catalog);await settle();expect(signal?.aborted).toBe(true);expect(draft.value.provider_config).toEqual({provider:'local'})})
