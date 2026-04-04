# Current Handoff

Last updated: 2026-04-04

## Summary

Baked texture upload is now fully wired into live sessions. The cloud avatar blocker has a concrete
implementation path — the remaining question is whether it works end-to-end against a live OpenSim
target.

## What Was Done This Session (2026-04-04)

**Firestorm pcap decoded inline (Python TCP stream reassembly):**
- Confirmed `UploadBakedTexture` CAP URL from seed cap LLSD
- Extracted 5 JPEG2000 baked texture blobs: `local/baked-cache/bake-{0..4}.j2k`
  - sizes: 112 KB, 96 KB, 21 KB, 244 KB, 284 KB
- Extracted 5 `AgentSetAppearance` payloads (ground truth appearance)
- Decoded 178-byte `TextureEntry` binary: located 5 baked UUID slots at byte offsets 19, 37, 55, 73, 91
- Created `local/baked-cache/appearance-fixture.json` with TE hex, wearable_data, visual_params, serial, size, and per-blob te_offset mappings

**Session wiring (commit cc17060):**
- `BakedAppearanceOverride` dataclass: patched TE bytes, wearable cache items, visual_params, serial_num, size
- `_load_and_upload_baked_textures()`: loads J2K blobs, uploads each via `UploadBakedTexture` CAP,
  patches TE at known UUID offsets with returned `new_asset` UUIDs
- Called from `_run_caps_prelude` after inventory fetch; result stored on `LiveCircuitSession.baked_appearance_override`
- `_drain_appearance_packets` now prefers `baked_appearance_override` over bootstrap/default fallback when set
- 148 tests pass

**Signal Log:**
- Claim #001 (Codex): endorsed by Claude ✓
- Claim #002 (Claude): open, pending next agent

## What Is Now Known

- Firestorm uploads exactly 5 J2K codestreams per login via `UploadBakedTexture` two-step flow
- TE blob is 178 bytes; baked UUID slots start at offset 19 (spacing 18 bytes between slots)
- The wiring is correct: if the live OpenSim accepts the uploads and returns `new_asset` UUIDs,
  those UUIDs will be in the `AgentSetAppearance` TE, and cloud state should clear
- `UploadBakedTexture` CAP must be advertised by the simulator — it is in the requested list

## What Remains Unknown

- Whether the local OpenSim instance honors `UploadBakedTexture` and returns real asset UUIDs
  (OpenSim does implement `UploadBakedTextureModule.cs` — probability: high)
- Whether sending foreign-avatar J2K blobs (from the Firestorm pcap) is accepted or rejected
- Whether `AgentCachedTextureResponse` will return non-zero IDs after successful upload, or whether
  the server needs a cache warm-up first
- Whether the TE offset patching is sufficient — server may require specific face-mask byte ordering

## What Was Verified

- All 148 tests pass after the session.py changes
- `_BAKED_CACHE_DIR` path resolves correctly to `local/baked-cache/` from the installed package location
- Fixture file and all 5 J2K blobs confirmed present at that path

## One Concrete Next Step

Run a live session against the local OpenSim target and inspect session output for:
```
caps[seed]=...,UploadBakedTexture,...
bake.uploaded blob=0 asset=<uuid>
bake.uploaded blob=1 asset=<uuid>
...
bake.override_ready serial=5 bakes=5
appearance.baked_override serial=5 te=178 vp=253 bakes=5
```

If `bake.override_ready` appears and the avatar is no longer cloud-like, the blocker is fixed.

If uploads succeed but avatar stays cloud, check:
- `appearance[cached_textures]` — are returned texture IDs non-zero?
- Whether OpenSim needs a second login/cached-check cycle after assets are registered
- Whether the TE face-mask entries need to match the specific face indices OpenSim expects

If `bake.upload_error` appears, the local OpenSim may not have `UploadBakedTexture` enabled —
check OpenSim config (`Caps_EnabledWhere`) or confirm the CAP appears in seed cap response.

## Notes For The Next Agent

- `local/baked-cache/appearance-fixture.json` is the ground truth for TE offsets and wearable data
- `local/baked-cache/bake-{0..4}.j2k` are the raw J2K blobs from the Firestorm capture
- `src/vibestorm/caps/upload_baked_texture_client.py`: the upload client (unchanged)
- `src/vibestorm/udp/session.py`: the session loop with wiring (commit cc17060)
- `docs/claude-handoff-2026-04-04.md`: detailed peer handoff with full pcap analysis and TE decoding
- The `SIGNAL_LOG.md` Claim #002 (Claude) is open and waiting for endorsement
