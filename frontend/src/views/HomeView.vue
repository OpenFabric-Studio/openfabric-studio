<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AppIcon from '../components/shared/AppIcon.vue'
import type { AppIconName } from '../components/shared/appIcons'

const { t } = useI18n()
const workspaces: { key: 'music' | 'voices' | 'video' | 'editor'; icon: AppIconName }[] = [
  { key: 'music', icon: 'ace_step' },
  { key: 'voices', icon: 'voice' },
  { key: 'video', icon: 'video' },
  { key: 'editor', icon: 'editor' },
]
const notes: { key: 'engines' | 'versions' | 'settings'; icon: AppIconName }[] = [
  { key: 'engines', icon: 'ace_step' },
  { key: 'versions', icon: 'yue2' },
  { key: 'settings', icon: 'settings' },
]
</script>

<template>
  <div class="home-overview">
    <section class="home-hero" aria-labelledby="home-title">
      <div class="hero-copy">
        <p class="eyebrow">{{ t('homeOverview.eyebrow') }}</p>
        <h1 id="home-title">
          {{ t('homeOverview.titleLead') }}
          <span>{{ t('homeOverview.titleAccent') }}</span>
        </h1>
        <p class="hero-intro">{{ t('homeOverview.intro') }}</p>
      </div>

      <!-- A static illustration of the creative tools, not live audio or engine status. -->
      <div class="hero-art" aria-hidden="true">
        <svg viewBox="0 0 500 360" fill="none" aria-hidden="true" focusable="false">
          <circle cx="256" cy="180" r="148" class="art-orbit" />
          <circle cx="256" cy="180" r="116" class="art-orbit" stroke-dasharray="3 9" />
          <path d="M22 228C139 347 396 317 483 103" class="art-orbit" />
          <path d="M89 41v18m-9-9h18 M451 273v18m-9-9h18" class="art-detail" />
          <circle cx="443" cy="85" r="4" class="art-dot" />
          <circle cx="54" cy="270" r="3" class="art-dot" />

          <g transform="rotate(9 350 218)">
            <rect x="246" y="143" width="207" height="151" rx="15" class="art-surface" />
            <rect x="258" y="155" width="183" height="127" rx="7" class="art-inset" />
            <path d="m259 261 58-61 37 39 29-24 57 47" class="art-landscape" />
            <circle cx="398" cy="186" r="12" class="art-sun" />
            <path d="M270 149h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h10 M270 288h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h10m12 0h10" class="art-film" />
          </g>

          <g transform="rotate(-9 208 133)">
            <rect x="61" y="66" width="292" height="135" rx="18" class="art-surface" />
            <path d="M77 133h262" class="art-orbit" />
            <path d="M85 127v12m12-23v34m12-49v64m12-82v95m12-74v58m12-29v3m12-26v49m12-72v96m12-118v143m12-116v90m12-59v28m12-10v-9m12-19v49m12-74v100m12-114v128m12-109v89m12-67v44m12-31v16m12-10v5" class="art-wave" />
          </g>

          <circle cx="155" cy="269" r="56" class="art-surface" />
          <circle cx="155" cy="269" r="45" class="art-inset" />
          <g class="art-mic">
            <rect x="144" y="238" width="22" height="39" rx="11" />
            <path d="M134 264v4a21 21 0 0 0 42 0v-4 M155 289v12 M146 301h18 M150 248h10 M150 255h10" />
          </g>
          <path d="m380 44 4 11 11 4-11 4-4 11-4-11-11-4 11-4z" class="art-star" />
        </svg>
      </div>
    </section>

    <section class="workspace-section" aria-labelledby="home-workspaces">
      <div class="section-heading">
        <h2 id="home-workspaces">{{ t('homeOverview.workspacesTitle') }}</h2>
        <p>{{ t('homeOverview.workspacesIntro') }}</p>
      </div>
      <div class="workspace-grid">
        <article v-for="(workspace, index) in workspaces" :key="workspace.key" class="workspace-card">
          <div class="card-top">
            <div class="workspace-icon"><AppIcon :name="workspace.icon" /></div>
            <span class="workspace-number" aria-hidden="true">0{{ index + 1 }}</span>
          </div>
          <h3>{{ t('homeOverview.workspaces.' + workspace.key + '.title') }}</h3>
          <p class="workspace-description">{{ t('homeOverview.workspaces.' + workspace.key + '.description') }}</p>
          <div class="workspace-destination">
            <p class="destination-label">{{ t('homeOverview.inMenu') }}</p>
            <p>{{ t('homeOverview.workspaces.' + workspace.key + '.destination') }}</p>
          </div>
        </article>
      </div>
    </section>

    <section class="studio-notes" aria-labelledby="home-notes">
      <h2 id="home-notes">{{ t('homeOverview.goodToKnow') }}</h2>
      <dl class="notes-grid">
        <div v-for="note in notes" :key="note.key" class="studio-note">
          <dt><AppIcon :name="note.icon" /><span>{{ t('homeOverview.' + note.key + '.title') }}</span></dt>
          <dd>{{ t('homeOverview.' + note.key + '.description') }}</dd>
        </div>
      </dl>
    </section>
  </div>
</template>

<style scoped>
.home-overview { padding: 16px 0 20px; }
.home-hero { display: grid; grid-template-columns: 1.18fr 1fr; align-items: center; gap: 24px; min-height: 352px; padding: 22px 0 38px; }
.hero-copy { min-width: 0; }
.eyebrow { margin-bottom: 22px; color: var(--color-text-dim); font-size: 11px; font-weight: 600; letter-spacing: .16em; text-transform: uppercase; }
h1 { font-size: clamp(36px, 3.5vw, 54px); font-weight: 600; line-height: 1.12; letter-spacing: -.055em; text-wrap: balance; }
h1 span { display: block; color: color-mix(in srgb, var(--color-accent2) 55%, var(--color-text)); }
.hero-intro { max-width: 440px; margin-top: 22px; color: var(--color-text-dim); font-size: 15px; line-height: 1.8; }
.hero-art { min-width: 0; background: radial-gradient(ellipse at center, color-mix(in srgb, var(--color-accent1) 13%, transparent), transparent 69%); }
.hero-art svg { display: block; width: 100%; max-width: 500px; margin: 0 auto; }
.art-orbit { stroke: var(--color-border); stroke-width: 1; }
.art-detail { stroke: var(--color-text-dim); stroke-width: 1.5; opacity: .55; }
.art-dot { fill: var(--color-accent2); opacity: .75; }
.art-surface { fill: var(--color-panel); stroke: color-mix(in srgb, var(--color-accent2) 28%, var(--color-border)); stroke-width: 1.5; }
.art-inset { fill: var(--color-panel-2); }
.art-wave { stroke: color-mix(in srgb, var(--color-accent2) 70%, var(--color-text)); stroke-width: 4; stroke-linecap: round; }
.art-landscape { stroke: var(--color-accent2); stroke-width: 2; stroke-linejoin: round; }
.art-sun { fill: color-mix(in srgb, var(--color-accent2) 40%, var(--color-panel-2)); }
.art-film { stroke: var(--color-text-dim); stroke-width: 2; opacity: .6; }
.art-mic { stroke: var(--color-text); stroke-width: 2; stroke-linecap: round; }
.art-star { fill: var(--color-accent2); }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px 24px; margin-bottom: 20px; }
h2 { font-size: 18px; font-weight: 600; letter-spacing: -.025em; }
.section-heading p { color: var(--color-text-dim); font-size: 13px; line-height: 1.6; }
.workspace-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.workspace-card { display: flex; flex-direction: column; min-width: 0; padding: 24px 22px 20px; border: 1px solid var(--color-border); border-radius: 14px; background: var(--color-panel); }
.card-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 25px; }
.workspace-icon { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border: 1px solid color-mix(in srgb, var(--color-accent2) 24%, var(--color-border)); border-radius: 12px; background: color-mix(in srgb, var(--color-accent1) 12%, transparent); color: color-mix(in srgb, var(--color-accent2) 45%, var(--color-text)); }
.workspace-number { color: var(--color-text-dim); font-size: 12px; font-variant-numeric: tabular-nums; }
h3 { margin-bottom: 10px; font-size: 20px; font-weight: 600; letter-spacing: -.025em; }
.workspace-description { flex: 1; color: var(--color-text-dim); font-size: 14px; line-height: 1.75; }
.workspace-destination { margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--color-border); font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; }
.destination-label { margin-bottom: 3px; color: var(--color-text-dim); font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
.studio-notes { margin-top: 36px; padding-top: 26px; border-top: 1px solid var(--color-border); }
.notes-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 28px; margin-top: 20px; }
dt { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 6px; font-size: 13px; font-weight: 500; }
dt > svg { margin-top: 2px; color: var(--color-text-dim); width: 17px; height: 17px; }
dd { margin-left: 29px; color: var(--color-text-dim); font-size: 13px; line-height: 1.75; }
@media (max-width: 1200px) {
  .workspace-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .home-hero { min-height: 310px; }
  h1 { font-size: 40px; }
  .section-heading { align-items: flex-start; flex-direction: column; gap: 6px; }
}
@media (max-width: 900px) {
  .home-hero { grid-template-columns: 1fr; gap: 0; padding-top: 12px; }
  .hero-intro { max-width: 540px; }
  .hero-art { max-width: 360px; margin: 4px auto 0; }
  .notes-grid { grid-template-columns: 1fr; gap: 20px; }
}
@media (max-width: 560px) {
  .home-overview { padding-top: 8px; }
  h1 { font-size: 36px; }
  .eyebrow { margin-bottom: 18px; font-size: 10px; }
  .hero-intro { font-size: 14px; margin-top: 18px; }
  .hero-art { max-width: 300px; margin-top: 8px; }
  .home-hero { padding-bottom: 24px; }
  .workspace-grid { grid-template-columns: 1fr; gap: 12px; }
  .workspace-card { padding: 20px; }
  .card-top { margin-bottom: 16px; }
  .workspace-destination { margin-top: 18px; }
  .studio-notes { margin-top: 28px; }
}
</style>
