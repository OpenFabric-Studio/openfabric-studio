import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/settings/SettingsPage.vue') },
    { path: '/ace-step', name: 'ace-step', component: () => import('./views/ace-step/AceStepPage.vue') },
    { path: '/ace-step/lora', name: 'ace-step-lora', component: () => import('./views/ace-step/LoraTrainingPage.vue') },
    { path: '/yue2', name: 'yue2', component: () => import('./views/yue2/Yue2Page.vue') },
    { path: '/voice-clone', name: 'voice-clone', component: () => import('./views/voice/VoiceClonePage.vue') },
    { path: '/video', name: 'video', component: () => import('./views/video/VideoPage.vue') },
    { path: '/editor', name: 'editor-projects', component: () => import('./views/editor/ProjectsListPage.vue') },
    { path: '/editor/:id', name: 'editor', component: () => import('./views/editor/EditorPage.vue'), props: true },
  ],
})

export default router
