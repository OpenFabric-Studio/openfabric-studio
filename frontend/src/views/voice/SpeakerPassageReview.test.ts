// @vitest-environment happy-dom
import {afterEach,expect,it,vi} from 'vitest'
import {createApp,h,nextTick,ref,type App} from 'vue'
import {createI18n} from 'vue-i18n'
import SpeakerPassageReview from './SpeakerPassageReview.vue'
import * as api from '../../api/speakerReview'
import type {SpeakerReferenceCandidate,SpeakerReviewCapability,SpeakerReview} from '../../api/contracts'
import en from '../../locales/en'
import {speakerReviewEn} from '../../locales/speakerReview'
vi.mock('../../api/speakerReview',()=>({getSpeakerCapability:vi.fn(),listSpeakerReferences:vi.fn(),listSpeakerReviews:vi.fn(),startSpeakerReview:vi.fn(),getSpeakerReview:vi.fn(),cancelSpeakerReview:vi.fn()}))
let app:App|undefined
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren();vi.resetAllMocks();vi.useRealTimers()})
async function settle(){for(let index=0;index<12;index++)await nextTick()}
const book='a'.repeat(32),passage='b'.repeat(32),identity='c'.repeat(64)
const reference:SpeakerReferenceCandidate={id:'d'.repeat(64),kind:'passage',source_id:'e'.repeat(32),chapter_index:1,revision:1,
  source_identity:'f'.repeat(64),render_snapshot_identity:'1'.repeat(64),audio_sha256:'2'.repeat(64),profile_id:'3'.repeat(32),speaker:'Narrator',renderer:'local',label:'Chapter 2, passage 1',audio_url:`/api/audiobooks/${book}/passages/${'e'.repeat(32)}/audio?revision=1`,duration_ms:4000}
it('requires a listened reference and explicit threshold before starting local screening',async()=>{
  vi.mocked(api.getSpeakerCapability).mockResolvedValue({available:true})
  vi.mocked(api.listSpeakerReferences).mockResolvedValue([reference]);vi.mocked(api.listSpeakerReviews).mockResolvedValue([])
  vi.mocked(api.startSpeakerReview).mockRejectedValue(new Error('test boundary'))
  app=createApp({render:()=>h(SpeakerPassageReview,{bookId:book,chapterIndex:0,passageId:passage,revision:1,renderIdentity:identity,active:true})}).use(createI18n({legacy:false,locale:'en',messages:{en:{...en,speakerReview:speakerReviewEn}}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root);await settle()
  expect(api.getSpeakerCapability).not.toHaveBeenCalled()
  const details=root.querySelector('details');if(!details)throw new Error('Missing review disclosure')
  details.open=true;details.dispatchEvent(new Event('toggle'));await settle()
  const button=[...root.querySelectorAll('button')].find(item=>item.textContent==='Compare speaker locally');if(!button)throw new Error('Missing local screening action')
  expect(button.disabled).toBe(true)
  const choice=root.querySelector('select');if(!(choice instanceof HTMLSelectElement))throw new Error('Missing reference selector')
  choice.value=reference.id;choice.dispatchEvent(new Event('change'));await settle()
  const check=root.querySelector('input[type=checkbox]'),threshold=root.querySelector('input[type=number]')
  if(!(check instanceof HTMLInputElement)||!(threshold instanceof HTMLInputElement))throw new Error('Missing explicit approval and calibration')
  check.checked=true;check.dispatchEvent(new Event('change'));await settle();expect(button.disabled).toBe(true)
  threshold.value='0.72';threshold.dispatchEvent(new Event('input'));await settle();expect(button.disabled).toBe(false)
  button.click();await settle()
  expect(api.startSpeakerReview).toHaveBeenCalledWith(book,0,passage,{revision:1,render_identity:identity,reference_id:reference.id,reference_reviewed:true,threshold:0.72},expect.any(AbortSignal))
})
it('aborts and ignores a late capability/reference load after passage switch',async()=>{
  let resolve:(value:SpeakerReviewCapability)=>void=()=>{throw new Error('No pending load')}
  vi.mocked(api.getSpeakerCapability).mockReturnValueOnce(new Promise(value=>{resolve=value})).mockResolvedValue({available:false})
  vi.mocked(api.listSpeakerReferences).mockResolvedValue([reference]);vi.mocked(api.listSpeakerReviews).mockResolvedValue([])
  const selected=ref(passage)
  app=createApp({render:()=>h(SpeakerPassageReview,{bookId:book,chapterIndex:0,passageId:selected.value,revision:1,renderIdentity:identity,active:true})}).use(createI18n({legacy:false,locale:'en',messages:{en:{...en,speakerReview:speakerReviewEn}}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root)
  const details=root.querySelector('details');if(!details)throw new Error('Missing disclosure')
  details.open=true;details.dispatchEvent(new Event('toggle'));await settle()
  const signal=vi.mocked(api.getSpeakerCapability).mock.calls[0]?.[0]
  selected.value='4'.repeat(32);await settle();expect(signal?.aborted).toBe(true)
  resolve({available:true});await settle()
  expect(root.querySelector('select')).toBeNull()
})
it('restores a completed hint and revalidates it without starting another analysis',async()=>{
  vi.useFakeTimers()
  const completed:SpeakerReview={id:'5'.repeat(32),book_id:book,chapter_index:0,passage_id:passage,revision:1,render_identity:identity,
    audio_sha256:'6'.repeat(64),reference,reference_reviewed:true,threshold:0.72,score:0.8,below_threshold:false,state:'completed',
    renderer_identity_verified:false,warnings:['speaker_renderer_unverified'],created_at:'2026-10-06T12:00:00Z',updated_at:'2026-10-06T12:00:00Z'}
  vi.mocked(api.getSpeakerCapability).mockResolvedValue({available:true});vi.mocked(api.listSpeakerReferences).mockResolvedValue([reference])
  vi.mocked(api.listSpeakerReviews).mockResolvedValue([completed]);vi.mocked(api.getSpeakerReview).mockResolvedValue({...completed,state:'stale',score:null,below_threshold:null})
  app=createApp({render:()=>h(SpeakerPassageReview,{bookId:book,chapterIndex:0,passageId:passage,revision:1,renderIdentity:identity,active:true})}).use(createI18n({legacy:false,locale:'en',messages:{en:{...en,speakerReview:speakerReviewEn}}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root)
  const details=root.querySelector('details');if(!details)throw new Error('Missing disclosure')
  details.open=true;details.dispatchEvent(new Event('toggle'));await settle()
  expect(root.textContent).toContain('Cosine similarity: 0.800')
  await vi.advanceTimersByTimeAsync(10000);await settle()
  expect(root.textContent).not.toContain('Cosine similarity:')
  expect(root.textContent).toContain('Inputs changed.')
  expect(api.startSpeakerReview).not.toHaveBeenCalled()
})
