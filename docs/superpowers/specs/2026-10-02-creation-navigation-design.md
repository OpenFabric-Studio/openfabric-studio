# Creation menu and music model selection

The user explicitly requested Create → Music, Audiobook, Video and Tools → LoRA Training, Voice Clone, with music model switching inside Music. Preserve the compact header, foldable sidebar, Help, Settings and informative Home.

## Navigation and routes

Music is one sidebar link. A shared `/music` parent presents a Music heading and two explicit model buttons above the existing ACE-Step and YuE generator views. Model routes are `/music/ace-step` and `/music/yue2` with their existing route names. Old `/ace-step` and `/yue2` addresses redirect to those routes so saved links and generated reference workflows continue to work. `/music` opens the currently active model when known, otherwise ACE-Step. Entering a route does not start or stop an engine; clicking a model button is the explicit startup/switch action. Selecting a model explains that the other engine stops.

The model selector uses ordinary labelled buttons with `aria-pressed` and translated status text, rather than automatic keyboard-activated tabs that would switch engines on arrow-key focus. Own pending navigation, disable duplicate/busy actions, and invalidate startup after teardown, cancelled navigation or navigation to another workspace. Keep header status controls read-only. Retain the LoRA running-engine prerequisite and point its explanation to Music.

Audiobook gets `/audiobooks` and its own heading, with no voice-mode tab bar. Reuse the existing VoiceClonePage component on both voice/book routes so visited panels and in-memory drafts remain owned by the same component across those destinations. Voice Clone shows Singing and Speech. The legacy `?mode=audiobooks` bookmark replaces its URL with `/audiobooks`, retaining unrelated queries. Cross-workspace activity notices still navigate to the appropriate destination, and hidden audio pauses. Gate singer query ownership so book routes cannot reset a singer draft or receive a singing upload's query updates.

Home's destination labels and music guide must reflect the new menu. Existing generated content, voice models, speech profiles, book jobs and backend contracts are unchanged. No new dependencies, caches, databases or engines are needed.

## Verification

Behavioral regressions cover exact menu grouping, active state/history, icon labels/tooltips/mobile drawer, passive Music navigation, explicit guarded model switching, legacy music routes, audiobook presentation, legacy book links and draft/background ownership between voice/book routes. Run focused and complete frontend tests, strict build, contracts check and diff review. Verify live desktop and narrow layouts through navigation only; avoid switching the user's real engines or generating outputs during browser checks. Independently review the integrated change and leave all work uncommitted.
