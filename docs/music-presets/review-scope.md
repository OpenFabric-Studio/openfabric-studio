# Review scope and unresolved implementation

All rows below are report proposals, not musician endorsements. No reviewer was engaged or authenticated, no sound pack was admitted, and no listening study was performed. The category and proposed assets come from report §6.4. The checked compiler fixtures are synthetic signals with synthetic declarations and must never be distributed or described as those instruments.

| # | Stable preset ID | Report title | Current representation with a compatible fixture kit |
| --- | --- | --- | --- |
| 1 | `guzheng_dawn` | Guzheng Dawn | Unreviewed gong candidate; fixed grid warns that free phrasing is approximated |
| 2 | `erhu_elegy` | Erhu Elegy | Unreviewed yu collection and sparse events; expressive slides/vibrato absent |
| 3 | `peking_opera_drums` | Peking Opera Drums | Unreviewed generic percussion grid; no school-specific score or invented jinghu notes |
| 4 | `guofeng_trap` | Guofeng Trap | Unreviewed half-time fusion candidate, quarter-note clock |
| 5 | `lion_dance` | Lion Dance | Blocked: a real acceleration envelope is not expressible by the current document |
| 6 | `cpop_ballad` | C-pop Ballad | Unreviewed heptatonic pitch sketch; full pop harmony/arrangement absent |
| 7 | `kyoto_koto` | Kyoto Koto | Unreviewed named in variant; fixed grid warns that free phrasing is approximated |
| 8 | `matsuri_taiko` | Matsuri Taiko | Unreviewed yo/percussion sketch, not a verified kuchi-shoga transcription |
| 9 | `lofi_kyoto` | Lo-fi Kyoto | Unreviewed in candidate and swung boom-bap sketch |
| 10 | `tokyo_city_pop` | Tokyo City Pop | Unreviewed major/pop sketch; complete extended-chord voicings absent |
| 11 | `okinawa_breeze` | Okinawa Breeze | Unreviewed Ryukyu candidate in 2/4 |
| 12 | `shamisen_rock` | Shamisen Rock | Unreviewed yo/rock sketch; idiomatic articulation absent |
| 13 | `gayageum_sanjo` | Gayageum Sanjo | Blocked: requested cycle progression and phrasing are not represented |
| 14 | `samulnori_fire` | Samulnori Fire | Unreviewed 12/8 grouping and separate drum/metal roles |
| 15 | `kgugak_pop` | K-Gugak Pop | Unreviewed pyeongjo equal-tempered approximation and pop sketch |
| 16 | `javanese_lancaran` | Javanese Lancaran | Blocked: no measured source ensemble tuning |
| 17 | `pelog_night` | Pelog Night | Blocked: no measured source tuning/selected pathet |
| 18 | `bali_kotekan` | Bali Kotekan | Blocked: no measured paired instrument tuning; no invented ombak |
| 19 | `gamelan_electronica` | Gamelan Electronica | Blocked: no measured source ensemble tuning |
| 20 | `raga_dusk` | Raga Dusk | Kalyan pitch sketch with explicit warning that raga phrase rules are absent |
| 21 | `bhangra_rush` | Bhangra Rush | Unreviewed Khamaj collection and dhol/tumbi sketch; no verified chaal |
| 22 | `bollywood_groove` | Bollywood Groove | Unreviewed Kafi film/pop sketch |
| 23 | `siam_court` | Siam Court | Blocked: no measured Thai instrument-set tuning |
| 24 | `isan_mor_lam` | Isan Mor Lam | Blocked: no source-specific khaen/phin tuning/mode |

All 15 named rhythm records remain `unreviewed`, including entries with recognizable structural metadata. Their generic fixture events do not count as implementing the required stroke vocabulary, ornaments, articulation transitions or musician-reviewed cycle. Pattern boundaries use the existing beat compiler; no second clock or renderer is introduced.

Verification currently covers bounded strict contracts, complete reference resolution, exact report inventory, fixture-backed compilation for five fixed seeds, repeatability, scale membership, timeline bounds, missing/wrong instruments, wrong hash-bound rights declarations, unavailable articulations and explicit refusal of unknown tuning/timing. It does not cover source sample accuracy, cultural recognition, native playback quality, real preview latency or a listening panel. The report's proposed recognition percentages and latency targets remain unevaluated.
