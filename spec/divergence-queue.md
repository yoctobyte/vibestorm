# Divergence Queue

Things found during development that belong in the `virtual-world-protocol`
documentation project and are not there yet.

**One line each. This is a pointer, not the content.** The evidence already
lives in a module docstring and usually a source-pin test; this file exists so
a docs session has an agenda instead of trying to remember, which is exactly
how `docs/reverse-engineered-protocol.md` went three months stale.

A growing queue is a visible signal. A forgotten discovery is not — that is the
whole reason this file is cheap enough to always update.

## The bar

Add an entry when an implementation behaves in a way the message template, the
published documentation, or a careful reader's expectation would get **wrong**,
*and* the failure is quiet. Loud failures teach themselves and do not need
documenting.

Not everything surprising qualifies. A layout that is merely undocumented is a
reference item, not a divergence.

## Queued

| Area | Divergence | Evidence lives in |
| --- | --- | --- |
| Object updates | Flexi softness is a 2-bit level split across the *top* bit of two bytes whose low 7 bits are tension/drag | `world/extra_params.py` |
| Object updates | Light intensity is carried in the colour's **alpha** channel, not opacity | `world/extra_params.py` |
| Object updates | `TextureEntry` face mask is MSB-first 7-bit groups, **not** LEB128 | `world/texture_entry.py` |
| Asset formats | Gesture and wearable formats have no parser named after them; the only structural readers are inside `UuidGatherer`, a UUID scraper | `assets/gesture.py`, `assets/wearable.py` |
| Asset formats | Animation assets end with four bytes OpenSim's own reader never looks at | `assets/animation.py` |
| Asset formats | Settings assets are LLSD *notation*; OpenSim never structurally parses them — its only handling is a regex scrape its own source marks `// BAD to do` | `test/fixtures/library/README.md` |
| Asset formats | Library notecards are plain UTF-8 with no container, unlike viewer-written ones | `assets/notecard.py` |
| Asset formats | A mesh decoder fills in defaults for absent data, so "field is populated" never means "the asset supplied it" | `assets/sl_mesh.py` |
| Parcels | Parcel bitmap bit order is LSB-first at index `y * edge + x` | `world/parcel_overlay.py` |
| Object properties | `ObjectProperties.CreationDate` is **microseconds** since the epoch; the template says only `U64`, and every other timestamp a reader meets is seconds | `udp/messages.py`, `tools/verify_object_properties.py` |
| Object lifecycle | `KillObject` names a linkset's **root only**; the children are never listed and a client must sweep them itself, or keep a phantom prim per linkset that ever leaves view | `world/models.py`, `test/test_world_kill_object.py` |
| Object lifecycle | OpenSim has no handler for `ObjectDelete` at all -- it logs `ignoring unhandled packet` and answers nothing, so a client cannot tell a refusal from a delete that worked. `ObjectDetach` is the way out of a region: it takes a prim into inventory | `tools/delete_prims.py`, `tools/probe_support.py` |
| Sun and time | `SimulatorViewerTimeMessage.SunDirection` arrives from OpenSim as `(0, 0, 0)`, every message. It is not a missing answer a client can test for with `None` -- it is a well-formed direction of length nothing, and a normalise steps over it into whatever fallback is behind. The sun in this viewer had therefore never moved in any session | `viewer3d/atmosphere.py`, `test/test_viewer3d_scene_environment.py` |
| Sun and time | `SimulatorViewerTimeMessage.UsecSinceStart` is not an uptime: it is a **Unix timestamp in microseconds**. Checked against the machine's own clock on 2026-09-06 -- 1788651750037967 is 01:42 that morning | `viewer3d/scene.py` |
| Sun and time | `SimulatorViewerTimeMessage.SunPhase` does **not** advance at a constant rate. Two runs twenty-odd minutes apart, each averaged over about 100 s, measured 2.1816e-4 and 4.3634e-4 rad/s -- a factor of exactly two -- on a region reporting `SecPerDay = 14400` both times. So it is not a clock a client can read: a single window's rate is that part of the day's rate. Nothing here depends on it; the day cycle's own `sun_rotation` track places the sun | `tools/probe_sun.py` |
| Environment | The `ExtEnvironment` capability answers **503** until the agent is actually in the region. Resolving it from the seed capability straight after login succeeds, and the fetch then fails in a way that reads like a broken URL rather than like being early | `udp/session.py`, `world/environment.py` |
| Environment | An `ExtEnvironment` day cycle's five tracks are **positional** -- 0 water, 1 the sky at ground level, 2 to 4 the sky above each `track_altitudes` entry. Nothing labels a track; only a frame carries a `type`, so the two can disagree | `world/environment.py` |
| Environment | The colour fields a person sees are nested under `legacy_haze`, beside `rayleigh_config` / `mie_config` / `absorption_config`, which describe the same sky as a physical model. OpenSim writes **both**, so a reader must choose rather than detect | `viewer3d/atmosphere.py` |
| Environment | Only *some* of a sky frame's fields are under `legacy_haze`. The cloud, star, moon and sun fields sit at the frame root beside it, so a reader that finds `ambient` nested and concludes the frame is nested reads every cloud field as absent and gets the defaults, silently | `world/environment.py` |
| Environment | `cloud_scroll_rate` is a **two**-component vector where nearly every other vector in a sky frame is three. A reader that assumes three either raises or pads a zero into a real axis | `world/environment.py` |
| Environment | `star_brightness` is not a continuum in OpenSim's default cycle: it is exactly 500 in both night keyframes and exactly 0 in all six daytime ones, with nothing in between. The 500 is not documented anywhere as a maximum, so a client has to pick a normalising constant and cannot infer one from a single frame | `viewer3d/atmosphere.py` |
| Environment | Inside one **water** keyframe -- a frame whose own `type` already says `water` -- the two fog fields are named `water_fog_color` and `water_fog_density` while every other field beside them (`fresnel_offset`, `scale_above`, `normal_scale`, `wave1_direction`) is not prefixed. A reader keying on the bare name finds no colour and draws the default sea, silently | `world/environment.py` |
| Environment | `wave1_direction` and `wave2_direction` are a heading *and* a speed in one two-vector: the direction is where the wave runs and the **length** is how fast. Nothing says so. A reader that normalises loses the speed; one that does not ties speed to wavelength, so a faster wave is also a shorter one | `viewer3d/atmosphere.py` |
| Environment | `fresnel_offset` and `fresnel_scale` are named for a physical model and are not physical values: the default cycle's offset is **0.5**, where a real water surface reflects about 0.02 straight down. Used as an index of refraction they give a sea twenty-five times too reflective, and it looks plausible rather than broken | `viewer3d/atmosphere.py` |
| Environment | `water_fog_density` is the fog seen from **under** the surface, not distance haze over it. It is the only density-shaped number in the water frame, so it is what a reader reaches for when the sea meets the sky in a hard line -- and it is a different quantity in a different medium | `viewer3d/perspective.py` |
| Environment | `water_fog_density` and `underwater_fog_mod` are a ratio, not a coefficient. Taken literally as an extinction per metre, the default cycle's 16 x 0.25 puts underwater visibility at **six centimetres**; nothing in the document says what the unit is, so a client has to supply its own reference distance and cannot derive one | `viewer3d/atmosphere.py` |
| Environment | A water keyframe carries `scale_above` **and** `scale_below`, and they differ by nearly seven times (0.03 against 0.2). A reader that finds one and uses it for both draws a surface that barely ripples seen from underneath, which is the side it ripples most on | `viewer3d/atmosphere.py` |

## Not divergences

Kept here so they are not re-queued. These are **sourcing gaps** — things this
tree cannot answer — and belong in the reference material, if anywhere, as
explicit unknowns:

- `PrimFlags`, the particle system block, `ChatSourceType` / `ChatAudibleLevel`,
  region flag bits LSL does not expose, and the visual-parameter id table. All
  libomv, which ships only as a DLL.

## Done

Moved to the docs project; listed so a later session does not re-queue them.

- Notecards stored as textures by `NewFileAgentInventory`
- Task script upload returning an item id in `new_asset`
- `ViewerAsset` query key not selecting the type
- OpenSim unable to read its own shortest notecards
- `LLSDAssetUploadError` arriving in two shapes
- Asset type vs inventory type divergence
- New notecards pointing at a shared empty asset
- Wire UUIDs being big-endian, and mis-decoding silently producing a valid UUID
- `ParcelProperties` request and reply on different transports
- Capability lifetime bound to agent presence
- The "already logged in" refusal being the disconnect
- `FinishedVia*` teleport flags never being set
- `GetObjectPhysicsData` limited to one object by a misplaced brace
- Hover text colour alpha inverted on the wire
- `ObjectPhysicsProperties` only echoing the viewer's own edit
- The compressed shape block moving `ProfileCurve`, shifting thirteen fields
- Mesh prims sent with rewritten shape values that do not match the stored object
- `ExtraParams` having no outer length prefix in the compressed block. **The
  queue line for this was wrong** — it claimed a 6-vs-7-byte header difference;
  the per-block header is 2+4 in both, and the real difference is the outer
  `Variable 1` prefix. Caught only because the protocol requires re-verifying
  from source at write time rather than trusting the queue line.
- `OwnerID` being unconditional, detached from the sound fields, and set for particles
- Trees and grass sent as a fixed 113-byte block with no shape and no owner

- `ObjectExtraParams` is viewer-to-sim only in OpenSim: `LLClientView` registers
  `HandleObjectExtraParams` and the tree contains no construction site for
  `ObjectExtraParamsPacket`, so a client's inbound parser for it can never fire.
  Prim feature blocks reach the viewer in the `ExtraParams` tail of
  `ObjectUpdate` instead. (2026-09-02)
- The `ObjectData` block of `ObjectExtraParams` is `Variable`, so it carries a
  u8 count before the first entry. Worth stating because a decoder written to
  run to the end of the body round-trips against its own synthetic packets and
  is never contradicted by a real one — see the point above. (2026-09-02)

- The turn control bits do not turn the avatar. Holding
  `AGENT_CONTROL_TURN_LEFT` against OpenSim for eight seconds left the reported
  yaw at exactly 0.00 degrees, while a yawed `BodyRotation` took effect on the
  next `AgentUpdate` and walking then followed the new facing (~12 m per six
  seconds on all four compass legs). The client owns its rotation; the bits
  drive the turn animation only. `tools/verify_avatar_turn.py`. (2026-09-03)
- `AgentUpdate`'s `BodyRotation` is a packed quaternion -- x, y and z on the
  wire, with w recovered as the non-negative root -- so it can only carry a yaw
  in [-pi, pi]. A yaw accumulated past half a turn has to be wrapped before
  packing or it comes back mirrored. (2026-09-03)

- An asset served over the `ViewerAsset` capability completed but was never
  announced on the bus: `WorldClient.on_session_event` published
  `AssetDataReady` only for the UDP `transfer.complete` path, while
  `asset.http.ok` -- the *preferred* path whenever the capability resolves,
  which is nearly always -- left the bytes sitting in `session.fetched_assets`
  with every waiter timing out. Worth recording because the failure is silent
  and reads as "the sim would not give us the asset". (2026-09-05)
- In-world item names routinely carry the file suffix already: the local test
  prim holds an item genuinely called `vibestorm-sync-88338.lsl`. Matching a
  file back to its row on `Path.stem` therefore misses, and a sync that misses
  does not stop -- it creates a *second* row beside the one it pulled from.
  Two items can also legitimately claim one file name (`notes` and
  `notes.lsl`), which no folder can represent. (2026-09-05)

- `UpdateTaskInventory` copying an item into a prim may **rename** it. When the
  prim already holds an item by that name the copy arrives as `<name> 1`, and
  nothing in the reply says so -- the assigned name is only recoverable by
  diffing the task inventory's item ids across the copy. A sync that trusts the
  name it asked for finds no matching row on the next run and creates a second
  item. (2026-09-05)
- There is no message that creates a notecard row inside a prim.
  `Scene.UpdateTaskInventory` rejects a zero item id, and an unknown one it
  looks up in *agent* inventory and copies in, so the only route is two hops:
  create it in agent inventory (`CreateInventoryItem` +
  `UpdateNotecardAgentInventory`), then copy it in. Scripts are the exception,
  not the rule -- `RezScript` has no counterpart for any other asset type.
  (2026-09-05)
| Environment | `sun_scale` and `moon_scale` are **1.0 in all eight keyframes** of OpenSim's default cycle. A client can read that 1.0 is the unchanged size, and nothing else: the direction, the range and whether the number is a radius or an area are all unwritten, and no capture of the default cycle can settle them | `viewer3d/atmosphere.py` |
| Environment | `cloud_shadow` is not about the clouds that are drawn. It is how much direct light the cloud layer keeps off the **ground**, it does not line up with any cloud the client renders, and nothing says whether it should also dim the ambient -- which is the difference between an overcast noon and a dusk | `viewer3d/perspective.py` |
| Environment | The water frame gives two wave directions and no wavelengths, and nothing says the two waves are different sizes. A reader that draws both at one length gets a perfect diamond lattice, which is invisible from a camera at eye height and unmistakable from above. The one thing that separates them is that the direction's length is a speed, and deep-water dispersion ties speed to wavelength -- a relation the document never mentions and cannot be got from `normal_scale` | `viewer3d/atmosphere.py` |
