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
| `gesture-can_we_move_along.bin` | gesture (21) | Line-based text format, undecoded so far |

Every one of the twelve library animations decodes; these two are the ends of
the range. All twelve end with four zero bytes that OpenSim's own reader never
looks at — see `assets/animation.py` for why they are surfaced and not named.
