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
