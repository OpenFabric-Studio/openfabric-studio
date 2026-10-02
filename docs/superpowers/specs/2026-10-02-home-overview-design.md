# Informative Home overview

The user requested a more interesting Home page that is only informative. Replace the existing model launch buttons, voice selector, track players and recent-project controls with a static guide to the studio. Navigation and engine actions remain in the already redesigned sidebar.

## Design

Use an editorial hero with the heading “Music, voices & moving pictures.”, a short plain-language introduction, and a static sound/voice/video illustration. Keep the existing dark theme, local Inter font, indigo accents and shared SVG icons. The illustration represents the creative tools; it is not a waveform measurement, progress display or live status.

Four read-only workspace cards describe Music, Voices, Video and Editor and identify their destinations in the left menu. A compact “Good to know” section explains the local library, audio versions, and Settings. Use verified existing features, with no quality claims, invented readiness, fake activity or metrics. Explain that models need to be installed separately without implying that Settings installs them.

Home has no buttons, forms, navigation links, audio players, job controls, model starts, polling or library requests. It remains readable when the backend is unavailable. The app shell's existing navigation, status tooltips, Help and attribution stay intact.

The hero and cards reflow at narrow widths, keep a clear heading order and useful text descriptions, and do not rely on hover. Hide decorative SVG from assistive technology. Use theme tokens and avoid decorative motion or new dependencies.

## Tradeoff

Removing recent playback makes Home a focused orientation page. Saved work and creation stay in their existing workspaces. A recent-work dashboard would conflict with the requested informational scope; no additional dashboard or reporting APIs are needed.

## Verification

Regression tests verify that Home describes all four workspaces, has no work controls or players, issues no library/model requests, and remains useful with unavailable services. Run frontend tests, strict production build and final diff checks. Inspect the live desktop and 375px page with the expanded/collapsed navigation, preserving user media and other uncommitted work.
