// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { i18n } from '../../i18n'
import * as profiles from '../../api/voiceProfiles'
import { videoProjectFixture } from './videoFixtures'
import type { VideoDraft } from './useVideoWorkspace'
import VideoSoundtrackPanel from './VideoSoundtrackPanel.vue'
vi.mock('../../api/voiceProfiles',()=>({listSpeechVoiceProfiles:vi.fn()}))
let app:App | undefined
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren();vi.restoreAllMocks()})
it('excludes cloud clones even with local references from the unquoted saved-voice action',async()=>{
  vi.mocked(profiles.listSpeechVoiceProfiles).mockResolvedValue([{id:'a'.repeat(32),name:'Local voice',consent_confirmed:true,renderer:'local',reference_audio_path:'reference.wav',created_at:'today',updated_at:'today'},
    {id:'b'.repeat(32),name:'Cloud clone',consent_confirmed:true,renderer:'openrouter',reference_audio_path:'reference.wav',created_at:'today',updated_at:'today'}])
  const draft:VideoDraft={name:'Video',mode:'generated',direction:'',character_lock:false,seed:0,settings:{},export_settings:{},shots:[],markers:[],overlays:[]}
  app=createApp({render:()=>h(VideoSoundtrackPanel,{project:videoProjectFixture(undefined,null),draft,readOnly:false})}).use(i18n)
  app.mount(document.body.appendChild(document.createElement('div')));for(let i=0;i<8;i++)await nextTick()
  expect([...document.querySelectorAll('[data-speech-voice] option')].map(item=>item.textContent)).toEqual(['Local voice'])
})
