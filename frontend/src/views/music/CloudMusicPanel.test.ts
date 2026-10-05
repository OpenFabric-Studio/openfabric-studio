// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { i18n, setLocale } from '../../i18n'
import * as provider from '../../api/openrouter'
import * as music from '../../api/cloudMusic'
import type { OpenRouterCatalog, OpenRouterQuote } from '../../api/generated'
import CloudMusicPanel from './CloudMusicPanel.vue'
vi.mock('../../api/openrouter', () => ({ getProviderStatus: vi.fn(),getProviderCatalog: vi.fn(),refreshProviderCatalog: vi.fn() }))
vi.mock('../../api/cloudMusic',()=>({listCloudMusic:vi.fn(),quoteCloudMusic:vi.fn(),submitCloudMusic:vi.fn(),cancelCloudMusic:vi.fn()}))
let app: App | undefined
const catalog: OpenRouterCatalog={models:[{id:'google/lyria-3-clip-preview',name:'Lyria Clip',kind:'music',fingerprint:'a'.repeat(64),prices:[{unit:'request',rate_usd:.04,source:'published_model_page',resolution:null,generate_audio:null}],supported_durations:[],supported_resolutions:[],supported_aspect_ratios:[],supported_sizes:[],supported_frame_images:[],supports_generate_audio:false,supports_seed:false,supports_voice_cloning:false,supported_voices:[],input_character_limit:4096,warnings:[]}],fingerprint:'b'.repeat(64),fetched_at:'2026-10-05',expires_at:99999999999}
const quote: OpenRouterQuote={id:'c'.repeat(32),kind:'music',model_id:catalog.models?.[0]?.id ?? '',model_fingerprint:'a'.repeat(64),request_fingerprint:'d'.repeat(64),estimated_usd:.04,expires_at:99999999999,ceiling_is_estimate:true,transfers:['prompt'],warnings:[]}
async function settle(){for(let n=0;n<10;n++)await nextTick()}
async function mount(){const el=document.body.appendChild(document.createElement('div'));const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:CloudMusicPanel},{path:'/settings',component:{render:()=>null}}]});await router.push('/');app=createApp(CloudMusicPanel).use(i18n).use(router);app.mount(el);await settle();return el}
function button(el: HTMLElement,text: string){const b=[...el.querySelectorAll('button')].find(b=>b.textContent?.trim()===text);if(!b)throw new Error(`Missing ${text}`);return b}
async function prompt(el: HTMLElement,text: string){const input=el.querySelector('textarea');if(!input)throw new Error('Missing prompt');input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));await settle()}
beforeEach(()=>{vi.clearAllMocks();setLocale('en');vi.mocked(provider.getProviderStatus).mockResolvedValue({enabled:true,credential_configured:true,credential_source:'session',secure_storage_available:false,estimate_limit_usd:1});vi.mocked(provider.getProviderCatalog).mockResolvedValue(catalog);vi.mocked(music.listCloudMusic).mockResolvedValue({jobs:[]});vi.mocked(music.quoteCloudMusic).mockResolvedValue(quote)})
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren()})
it('loads cached capabilities and job history without automatic paid generation',async()=>{const el=await mount();expect(el.textContent).toContain('Experimental');expect(music.submitCloudMusic).not.toHaveBeenCalled();expect(provider.refreshProviderCatalog).not.toHaveBeenCalled()})
it('requires explicit quote and transfer approval and invalidates it on edits',async()=>{const el=await mount();await prompt(el,'Warm jazz');button(el,'Review estimate').click();await settle();expect(music.quoteCloudMusic).toHaveBeenCalledWith({model:'google/lyria-3-clip-preview',prompt:'Warm jazz',seed:null},expect.any(AbortSignal));expect(el.textContent).toContain('$0.040');expect(button(el,'Generate cloud music').disabled).toBe(true);const check=el.querySelector('input[type=checkbox]');if(!(check instanceof HTMLInputElement))throw new Error('Missing approval');check.checked=true;check.dispatchEvent(new Event('change',{bubbles:true}));await settle();expect(button(el,'Generate cloud music').disabled).toBe(false);await prompt(el,'Changed prompt');expect(el.textContent).not.toContain('$0.040');expect(music.submitCloudMusic).not.toHaveBeenCalled()})
it('does not expose internal failure details',async()=>{vi.mocked(provider.getProviderStatus).mockRejectedValue(new Error('/private/key'));const el=await mount();expect(el.textContent).not.toContain('/private/key');expect(el.textContent).toContain('Could not load')})
