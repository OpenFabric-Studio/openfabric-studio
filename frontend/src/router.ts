import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useOrchestratorStore } from './stores/orchestrator'

export const appRoutes: RouteRecordRaw[] = [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/settings/SettingsPage.vue') },
    {
      path: '/music', component: () => import('./views/music/MusicPage.vue'),
      children: [
        { path: '', name: 'music', redirect: () => ({ name: useOrchestratorStore().activeModel === 'yue2' ? 'yue2' : 'ace-step' }) },
        { path: 'ace-step', name: 'ace-step', component: () => import('./views/ace-step/AceStepPage.vue') },
        { path: 'yue2', name: 'yue2', component: () => import('./views/yue2/Yue2Page.vue') },
      ],
    },
    { path: '/ace-step', redirect: to => ({ name: 'ace-step', query: to.query, hash: to.hash }) },
    { path: '/ace-step/lora', name: 'ace-step-lora', component: () => import('./views/ace-step/LoraTrainingPage.vue') },
    { path: '/yue2', redirect: to => ({ name: 'yue2', query: to.query, hash: to.hash }) },
    { path: '/voice-clone', name: 'voice-clone', component: () => import('./views/voice/VoiceClonePage.vue') },
    { path: '/audiobooks', name: 'audiobooks', component: () => import('./views/voice/VoiceClonePage.vue'), props: { audiobooksOnly: true } },
    { path: '/video', name: 'video', component: () => import('./views/video/VideoPage.vue') },
    { path: '/editor', name: 'editor-projects', component: () => import('./views/editor/ProjectsListPage.vue') },
    { path: '/editor/:id', name: 'editor', component: () => import('./views/editor/EditorPage.vue'), props: true },
]

const router = createRouter({ history: createWebHistory(), routes: appRoutes })

export default router
