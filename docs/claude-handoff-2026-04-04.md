# Claude Handoff 2026-04-04

This document is for a fresh reasoning pass by another agent. The main goal is to avoid spending
another round rediscovering the current state of the OpenSim/SL appearance problem.

## Read Order

Start here, then keep these open:

1. `README.md`
2. `projectstate.md`
3. `docs/current-handoff.md`
4. `docs/reverse-engineered-protocol.md`
5. `docs/opensim-source-map.md`
6. this file

## Executive Summary

Vibestorm is no longer blocked on basic login, UDP, object updates, or simulator push semantics.

Those parts are good enough now:

- XML-RPC login works
- seed caps work
- `EventQueueGet` works
- UDP handshake and bounded sessions work
- object delivery works well enough to receive:
  - self avatar
  - other avatars
  - nearby prims
  - terse-only avatars in some sessions
- logout is now explicit via `LogoutRequest`

The remaining major blocker is avatar appearance:

- the avatar still appears as a cloud / Ruth in the viewer
- `AgentWearablesUpdate` arrives
- self `AvatarAppearance` arrives
- Current Outfit resolution works
- `AgentCachedTextureResponse` still returns only zero texture IDs
- object/world delivery is therefore no longer the likely root cause

The strongest current hypothesis is:

- remaining failure is in baked appearance handling
- specifically real baked texture availability, upload, or cache alignment

## What Was Tried Already

### Session/bootstrap side

Vibestorm now mirrors the normal viewer startup more closely:

- XML-RPC `login_to_simulator`
- bind UDP socket before seed caps so the real local UDP port is known
- seed caps POST with `X-SecondLife-UDP-Listen-Port`
- early `EventQueueGet`
- early `SimulatorFeatures`
- startup inventory fetches for:
  - inventory root
  - Current Outfit Folder
  - COF source item resolution through `FetchInventory2`

### Appearance side

Implemented already:

- outbound `AgentWearablesRequest`
- inbound `AgentWearablesUpdate`
- outbound `AgentCachedTexture`
- inbound `AgentCachedTextureResponse`
- inbound `AvatarAppearance`
- outbound `AgentIsNowWearing`
- outbound `AgentSetAppearance`

Then improved further:

- keep login `packed_appearance` from XML-RPC bootstrap
- preserve:
  - `te8`
  - `visualparams`
  - `serial`
  - `height`
  - `bakedcache`
  - `bc8`
- if no richer self `AvatarAppearance` has arrived yet, use login `packed_appearance` as the
  fallback baseline for `AgentSetAppearance`

### What changed behavior-wise

These changes materially improved the client:

- second avatar can now promote to full `ObjectUpdate`
- object traffic is no longer the main bottleneck
- COF resolution is working
- clean logout now happens

But the cloud state still persists.

## What Is Most Important Right Now

Do not spend more time on generic UDP object work unless new evidence appears.

That area was the main uncertainty earlier, but the current evidence points elsewhere.

The likely bottleneck is one of:

1. real baked texture bytes are never being uploaded
2. cached texture IDs sent by the client do not correspond to currently valid OpenSim cache items
3. login `packed_appearance` is still insufficient by itself because the simulator expects baked
   assets to exist in local asset cache
4. some supporting asset retrieval path is still missing:
   - `ViewerAsset`
   - `GetTexture`
   - or related baked-texture capability choreography

## Critical Source Findings

These are the pieces that matter most.

### OpenSim `UploadBakedTextureModule`

File:

- `opensim-source/OpenSim/Region/ClientStack/Linden/Caps/UploadBakedTextureModule.cs`

Confirmed flow:

1. client `POST`s LLSD to `UploadBakedTexture`
2. server returns LLSD:
   - `state=upload`
   - `uploader=<one-shot-url>`
3. client `POST`s raw baked texture bytes to that uploader URL
4. server stores the asset locally and returns LLSD:
   - `state=complete`
   - `new_asset=<uuid>`

Vibestorm now has a client for this exact flow in:

- `src/vibestorm/caps/upload_baked_texture_client.py`

It is tested, but not yet wired into live sessions.

### OpenSim `HandleAgentTextureCached`

File:

- `opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`

Important behavior:

- OpenSim compares the client-sent `AgentCachedTexture` cache ID against the server-side stored
  `WearableCacheItem.CacheId`
- if they match, it returns the baked `TextureID`
- if they do not match, it returns zero

This explains why “just send zero cache IDs” was never going to clear the cloud state.

### OpenSim `AvatarFactoryModule.UpdateBakedTextureCache`

File:

- `opensim-source/OpenSim/Region/CoreModules/Avatar/AvatarFactory/AvatarFactoryModule.cs`

Important behavior:

- if cache items are absent, baked cache update is skipped
- if cache items are present but baked texture assets are not available locally, OpenSim zeroes the
  cache entries and may send `RebakeAvatarTextures`

This aligns well with the live symptom:

- `AgentCachedTextureResponse` returns zeros
- avatar remains cloud-like

### OpenSim login `packed_appearance`

Files:

- `opensim-source/OpenSim/Framework/AgentCircuitData.cs`
- `opensim-source/OpenSim/Framework/AvatarAppearance.cs`

Important behavior:

- login/bootstrap may already provide a packed appearance map
- that packed appearance may include:
  - `te8`
  - `visualparams`
  - `serial`
  - `height`
  - `bakedcache`
  - `bc8`

Vibestorm was previously underusing this. That is now fixed.

## Real Viewer Evidence

There is a Firestorm capture under:

- `local/viewer-captures/20260404T100148Z-viewer-login.pcap`
- `local/viewer-captures/20260404T100148Z-viewer-login.tcpdump.txt`
- `local/viewer-captures/20260404T100148Z-viewer-login.meta.txt`

Most useful conclusion:

- a real viewer does not jump directly from login to UDP
- it performs seed caps first, then early CAPS requests, then starts UDP

This already informed the current Vibestorm bootstrap.

If you want to push further, that capture is the right place to compare:

- appearance-related CAPS after login
- possible baked/upload calls
- asset requests tied to appearance resolution

## Current Live Diagnostics To Trust

The session report now emits compact signals that matter.

Look for:

- `caps[seed]=...`
- `appearance[bootstrap]=packed:1`
- `appearance[inventory]=...`
- `appearance[cof]=...`
- `appearance[cof_resolved]=...`
- `appearance[wearables]=...`
- `appearance[cached_textures]=...`
- `appearance[avatar]=...`
- `appearance[self_avatar]=...`

The key discriminator now is:

- if `appearance[bootstrap]=packed:1` and `appearance[cached_textures]=... non_zero:0`, but the
  avatar is still cloud-like, then bootstrap packed appearance is not enough

That should push the next agent toward real bake upload or asset-path work.

## Files Most Relevant For Next Work

### Vibestorm

- `src/vibestorm/login/client.py`
- `src/vibestorm/login/models.py`
- `src/vibestorm/caps/client.py`
- `src/vibestorm/caps/inventory_client.py`
- `src/vibestorm/caps/upload_baked_texture_client.py`
- `src/vibestorm/udp/session.py`
- `src/vibestorm/udp/messages.py`
- `src/vibestorm/app/cli.py`

### Tests

- `test/test_login_client.py`
- `test/test_inventory_caps_client.py`
- `test/test_udp_session.py`
- `test/test_cli_session_report.py`
- `test/test_upload_baked_texture_client.py`

### OpenSim source

- `opensim-source/OpenSim/Region/ClientStack/Linden/Caps/UploadBakedTextureModule.cs`
- `opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`
- `opensim-source/OpenSim/Region/CoreModules/Avatar/AvatarFactory/AvatarFactoryModule.cs`
- `opensim-source/OpenSim/Framework/AvatarAppearance.cs`
- `opensim-source/OpenSim/Framework/AgentCircuitData.cs`

## Repo State

There are important uncommitted changes in the worktree right now. Do not assume `git status` is
clean.

Tracked modified files include:

- `docs/current-handoff.md`
- `docs/reverse-engineered-protocol.md`
- `src/vibestorm/app/cli.py`
- `src/vibestorm/caps/inventory_client.py`
- `src/vibestorm/login/client.py`
- `src/vibestorm/login/models.py`
- `src/vibestorm/udp/messages.py`
- `src/vibestorm/udp/session.py`
- multiple tests

Untracked but intentional:

- `opensim-source/`
- `test/fixtures/live/ObjectUpdate/...`
- `local/session-reports/...`
- `local/viewer-captures/...`

Do not remove those.

## Tests Already Passing

Locally verified during the latest pass:

- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_login_client.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_session.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_cli_session_report.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_upload_baked_texture_client.py' -v`

Earlier suites in this branch also passed and are recorded in `docs/current-handoff.md`.

## Suggested Questions For Claude

These are the questions most worth a fresh reasoning pass:

1. Is `packed_appearance` plus current `AgentSetAppearance` still semantically incomplete in a way
   that guarantees cloud state, even before bake upload?
2. From OpenSim source plus viewer traces, what is the smallest valid route to non-cloud avatar
   appearance:
   - reuse existing baked IDs?
   - upload baked textures?
   - fetch wearable assets and bake locally?
3. Is there a simpler interim step involving `ViewerAsset` or `GetTexture` that would let Vibestorm
   validate the baked path without implementing a full local baker?
4. Is the right next implementation actually to react to `RebakeAvatarTextures` if it appears,
   rather than proactively uploading?

## My Best Current Guess

The next agent should probably focus on one of these, in order:

1. verify whether recent live runs now show `appearance[bootstrap]=packed:1`
2. if yes and still cloud:
   inspect whether login `packed_appearance.bakedcache` contains usable non-zero texture IDs
3. compare Firestorm capture for post-login appearance/baked capability calls
4. only then decide whether to auto-wire `UploadBakedTexture`

My bias is that blind upload of arbitrary bytes would not be a useful next step. A better step is:

- identify a trustworthy baked texture byte source first
- then connect that source to the already-tested uploader client

## Concrete Next Step

Run one fresh:

```bash
./tools/run_session_forensics.sh 90
```

Then use that result plus the above source files to decide whether the next patch should be:

- appearance-state interpretation
- baked upload wiring
- or asset retrieval support
