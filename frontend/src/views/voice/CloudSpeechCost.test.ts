// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import { createI18n } from 'vue-i18n'
import CloudSpeechCost from './CloudSpeechCost.vue'
import type { CloudSpeechApproval, CloudSpeechQuote } from '../../api/contracts'
let app: App | undefined
afterEach(() => { app?.unmount(); document.body.replaceChildren(); vi.restoreAllMocks() })
async function settle() { for (let i=0;i<8;i++) await nextTick() }
const quote: CloudSpeechQuote = { id:'a'.repeat(32),estimated_usd:0.04,request_count:3,models:['hexgrad/kokoro-82m'],transfers:['text'],expires_at:Date.now()/1000+300 }
it('requires the shown estimate and explicit transfer check before emitting paid authorization',async()=>{
  const load=vi.fn().mockResolvedValue(quote), approvals: Array<CloudSpeechApproval|null>=[]
  app=createApp({render:()=>h(CloudSpeechCost,{enabled:true,inputKey:'draft1',load,onApproval:value=>approvals.push(value)})}).use(createI18n({legacy:false,locale:'en',messages:{en:{cloudSpeech:{estimate:'Estimate cost',approve:'Approve transfer'}}}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root);await settle()
  expect(root.querySelector('input')).toBeNull()
  root.querySelector('button')?.click();await settle()
  expect(load).toHaveBeenCalledWith(expect.any(AbortSignal))
  expect(approvals.some(value=>value!==null)).toBe(false)
  const check=root.querySelector('input');if(!check)throw new Error('Missing transfer approval')
  check.checked=true;check.dispatchEvent(new Event('change'));await settle()
  expect(approvals.at(-1)).toEqual({quote_id:quote.id,transfers_confirmed:true})
})
it('discards a late cost response when the script or selected voice changes',async()=>{
  let resolve:(value:CloudSpeechQuote)=>void=()=>{throw new Error('Missing deferred quote')}
  const load=vi.fn().mockReturnValue(new Promise<CloudSpeechQuote>(release=>{resolve=release})), key=ref('one')
  app=createApp({render:()=>h(CloudSpeechCost,{enabled:true,inputKey:key.value,load})}).use(createI18n({legacy:false,locale:'en',messages:{en:{}}}))
  const root=document.body.appendChild(document.createElement('div'));app.mount(root);root.querySelector('button')?.click();await settle()
  key.value='two';await settle();resolve(quote);await settle()
  expect(root.querySelector('input')).toBeNull()
  expect(root.textContent).not.toContain('0.0400')
})
