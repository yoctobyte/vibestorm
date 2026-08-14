# Library asset fixtures

Real assets from the OpenSim grid library, fetched over the `ViewerAsset`
capability on 2026-08-14 with `./run.sh inventory-walk --library` to find them.

They are here because the alternative is a fixture built from the layout the
decoder implements, which tests nothing — it agrees with the decoder by
construction. These bytes were written by OpenSim.

| File | Type | Why this one |
| --- | --- | --- |
| `animation-place_marker.bin` | animation (20) | Smallest library animation. A pure pose: 14 joints, rotations only, no position keys |
| `animation-bouncy_ball_super.bin` | animation (20) | Largest, and the one that *moves* — 43 position keyframes across 19 joints. `place_marker` alone left the position quantisation range untested |
| `notecard-Welcome.bin` | notecard (7) | Plain UTF-8, **not** a `Linden text version 2` container. Worth keeping as evidence that library notecards and viewer-created ones differ |
| `script-Default.bin` | LSL text (10) | Plain UTF-8 source, no container |
| `gesture-can_we_move_along.bin` | gesture (21) | Line-based text: one animation step on the `/bored` trigger. The only real gesture available, so it exercises exactly one of the four step types |
| `clothing-Shirt.bin` | clothing (5) | `LLWearable version 22`, wearable type 4, 10 parameters, 1 texture |
| `bodypart-Hair.bin` | body part (13) | Same format under a different inventory type — the pair is what makes "one format, two types" a tested claim rather than an assumption. 90 parameters, so the counted-list handling is exercised at nine times the shirt's size |
| `settings-Default_Water.bin` | settings (56) | **Not decoded.** LLSD *notation* (`<? llsd/notation ?>`), a third serialisation our LLSD parser does not read. OpenSim never structurally parses it either — its only handling is a regex scrape its own source marks `// BAD to do`. Kept as the evidence for that |

Every one of the twelve library animations decodes; these two are the ends of
the range. All twelve end with four zero bytes that OpenSim's own reader never
looks at — see `assets/animation.py` for why they are surfaced and not named.

The gesture and wearable fixtures were fetched while both formats were still
believed unsourceable, to keep the bytes for whenever a source turned up. The
source was already in the tree: `UuidGatherer.cs` walks both, field by
commented field, as part of collecting the asset ids they reference. Fetching
the bytes first is what made it worth looking again.
