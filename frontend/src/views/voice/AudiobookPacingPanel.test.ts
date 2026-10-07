// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import { audiobookReviewEn } from '../../locales/audiobookReview'
const en = { audiobookReview: audiobookReviewEn }
import * as timing from '../../api/narrationTiming'
import * as workflow from '../../api/audiobookWorkflow'
import AudiobookPacingPanel from './AudiobookPacingPanel.vue'
import type { AudiobookBook, AudiobookJob, AudiobookPassagesResponse } from '../../api/contracts'
vi.mock('../../api/narrationTiming', () => ({ updatePacing: vi.fn() }))
vi.mock('../../api/audiobookWorkflow', () => ({ listPassages: vi.fn() }))
const book: AudiobookBook = { id:'a'.repeat(32), title:'Book', profile_id:'b'.repeat(32),chapter_count:1,status:'done',created_at:'now',updated_at:'now',passage_gap_ms:100,speaker_change_gap_ms:200 }
const job: AudiobookJob = { id:'c'.repeat(32),book_id:book.id,chapter_index:0,chapter_title:'One',status:'done',created_at:'now',updated_at:'now',revision:3 }
const passages: AudiobookPassagesResponse = { book_id:book.id,chapter_index:0,revision:3,passages:[{id:'d'.repeat(32),section_index:0,text:'First words',profile_id:book.profile_id,speaker:'Narrator',start_ms:0,end_ms:4000,status:'done',gap_after_ms:null,effective_gap_after_ms:300}] }
let app: App|undefined
beforeEach(()=>{vi.clearAllMocks();vi.mocked(workflow.listPassages).mockResolvedValue(passages);vi.mocked(timing.updatePacing).mockResolvedValue({...book,status:'queued'})})
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren()})
async function settle(){for(let i=0;i<8;i++)await nextTick()}
async function mount(){const active=ref(true);app=createApp({render:()=>h(AudiobookPacingPanel,{book,jobs:[job],active:active.value})});app.use(createI18n({legacy:false,locale:'en',messages:{en}}));const container=document.body.appendChild(document.createElement('div'));app.mount(container);await settle();return{container,active}}
async function click(container:HTMLElement,label:string){const element=[...container.querySelectorAll('button')].find(item=>item.textContent?.trim()===label);if(!element)throw new Error('Missing '+label);element.click();await settle()}
it('shows composition-only pacing and retains every expected chapter revision',async()=>{const{container}=await mount();await click(container,'Save pacing and reassemble');expect(timing.updatePacing).toHaveBeenCalledWith(book.id,{passage_gap_ms:100,speaker_change_gap_ms:200,chapters:[{chapter_index:0,revision:3,passages:[]}]},expect.any(AbortSignal));expect(container.textContent).toContain('Speech is not regenerated')})
it('submits an explicit zero override instead of silently restoring the default',async()=>{const{container}=await mount();await click(container,'Load passage pauses');const checkbox=container.querySelector('input[type=checkbox]');if(!(checkbox instanceof HTMLInputElement))throw new Error('Missing default toggle');checkbox.checked=false;checkbox.dispatchEvent(new Event('change',{bubbles:true}));await settle();const input=container.querySelector('[data-passage-gap]');if(!(input instanceof HTMLInputElement))throw new Error('Missing pause field');input.value='0';input.dispatchEvent(new Event('input',{bubbles:true}));await settle();await click(container,'Save pacing and reassemble');expect(vi.mocked(timing.updatePacing).mock.calls[0]?.[1].chapters[0]?.passages).toEqual([{passage_id:'d'.repeat(32),gap_after_ms:0}])})
it('aborts late passage results when hidden',async()=>{let resolve:((value:AudiobookPassagesResponse)=>void)|undefined;vi.mocked(workflow.listPassages).mockImplementation(()=>new Promise(done=>{resolve=done}));const{container,active}=await mount();await click(container,'Load passage pauses');const signal=vi.mocked(workflow.listPassages).mock.calls[0]?.[2];active.value=false;await settle();expect(signal?.aborted).toBe(true);resolve?.(passages);await settle();expect(container.textContent).not.toContain('First words')})
