import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

export const appRoutes: RouteRecordRaw[] = [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/settings/SettingsPage.vue') },
    {
      path: '/music', component: () => import('./views/music/MusicPage.vue'),
      children: [
        { path: '', name: 'music', component: () => import('./views/music/MusicQueuePage.vue') },
        { path: 'beats', name: 'music-beats', component: () => import('./views/music/BeatMakerPage.vue'), props: route => ({ beatId: typeof route.query.beat_id === 'string' ? route.query.beat_id : undefined, revision: typeof route.query.revision === 'string' ? route.query.revision : undefined }) },
        { path: 'voice-replacement', name: 'music-voice-replacement', component: () => import('./views/music/ReplacementPage.vue'), props: route => ({ planId: typeof route.query.plan_id === 'string' ? route.query.plan_id : undefined }) },
        { path: 'beat-song-mix', name: 'music-beat-song-mix', component: () => import('./views/music/BeatSongMixPage.vue'), props: route => ({ planId: typeof route.query.plan_id === 'string' ? route.query.plan_id : undefined }) },
        { path: 'mashups', name: 'music-mashups', component: () => import('./views/music/MashupPage.vue'), props: route => ({ mashupId: typeof route.query.mashup_id === 'string' ? route.query.mashup_id : undefined }) },
        { path: 'ace-step', name: 'ace-step', component: () => import('./views/ace-step/AceStepPage.vue') },
        { path: 'openrouter', name: 'openrouter-music', component: () => import('./views/music/CloudMusicPanel.vue') },
        { path: 'yue2', name: 'yue2', component: () => import('./views/yue2/Yue2Page.vue') },
        { path: 'stable-audio3', name: 'stable_audio3', component: () => import('./views/music/LocalMusicPanel.vue'), props: { engine: 'stable_audio3' } },
        {
          path: 'lora', component: () => import('./views/music/LoraWorkspacePage.vue'),
          children: [
            { path: '', name: 'music-lora', component: () => import('./views/music/LoraOverviewPage.vue') },
            { path: 'ace-step', name: 'music-lora-ace-step', component: () => import('./views/ace-step/LoraTrainingPage.vue') },
            { path: 'stable-audio3', name: 'music-lora-stable-audio3', component: () => import('./views/music/MusicLoraPage.vue'), props: { engine: 'stable_audio3' } },
            { path: 'yue2', name: 'music-lora-yue2', component: () => import('./views/music/MusicLoraPage.vue'), props: { engine: 'yue2' } },
          ],
        },
      ],
    },
    { path: '/ace-step', redirect: to => ({ name: 'ace-step', query: to.query, hash: to.hash }) },
    { path: '/ace-step/lora', name: 'ace-step-lora', redirect: to => ({ name: 'music-lora-ace-step', query: to.query, hash: to.hash }) },
    { path: '/yue2', redirect: to => ({ name: 'yue2', query: to.query, hash: to.hash }) },
    { path: '/voice-clone', name: 'voice-clone', component: () => import('./views/voice/VoiceClonePage.vue') },
    { path: '/audiobooks', name: 'audiobooks', component: () => import('./views/voice/VoiceClonePage.vue'), props: { audiobooksOnly: true } },
    { path: '/video', name: 'video', component: () => import('./views/video/VideoPage.vue') },
    { path: '/editor', name: 'editor-projects', component: () => import('./views/editor/ProjectsListPage.vue') },
    { path: '/editor/:id', name: 'editor', component: () => import('./views/editor/EditorPage.vue'), props: true },
]

const router = createRouter({ history: createWebHistory(), routes: appRoutes })

export default router
