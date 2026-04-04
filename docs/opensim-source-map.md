# OpenSim Source Map

Date: 2026-04-04

This is a practical map of the local OpenSim checkout at
[`opensim-source`](/home/rene/vibestorm/opensim-source).

It is not a full architecture document. The goal is simply to help future agents find the right
files quickly when answering a specific protocol question.

## High-Value Areas

### UDP / Viewer Protocol

- [`opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
  - main LLUDP send/receive implementation
  - packet handlers such as `AgentUpdate`, `AgentThrottle`, `ObjectExtraParams`,
    `RequestObjectPropertiesFamily`
  - send-side builders for `ObjectUpdate`, `ImprovedTerseObjectUpdate`, `KillObject`,
    `AvatarAppearance`, `ObjectPropertiesFamily`
  - best first stop for “what exact bytes does OpenSim serialize?”

- [`opensim-source/OpenSim/Framework/IClientAPI.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Framework/IClientAPI.cs)
  - shared client interface
  - useful for canonical names and responsibilities
  - exposes events such as `OnSetAppearance`, `OnRequestWearables`,
    `OnCachedTextureRequest`, `OnUpdateExtraParams`

### Scene / Interest Management

- [`opensim-source/OpenSim/Region/Framework/Scenes/Scene.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/Scene.cs)
  - scene-wide configuration and behavior
  - loads `[InterestManagement]` config including:
    - `UpdatePrioritizationScheme`
    - `ReprioritizationEnabled`
    - `ReprioritizationInterval`
    - `RootReprioritizationDistance`
    - `ObjectsCullingByDistance`

- [`opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs)
  - avatar/session state
  - `SendInitialData()`
  - `HandleAgentUpdate()`
  - camera and draw-distance behavior
  - avatar full-data / appearance / animation send paths
  - best first stop for “what does the server wait for before sending the world?”

- [`opensim-source/OpenSim/Region/Framework/Scenes/SceneGraph.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/SceneGraph.cs)
  - object graph routing
  - property-family request flow entrypoint

- [`opensim-source/OpenSim/Region/Framework/Scenes/SceneObjectGroup.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/SceneObjectGroup.cs)
  - object-group behavior
  - object property-family response servicing

## Broad Layout

- [`opensim-source/OpenSim/Region`](/home/rene/vibestorm/opensim-source/OpenSim/Region)
  - simulator/runtime code
- [`opensim-source/OpenSim/Framework`](/home/rene/vibestorm/opensim-source/OpenSim/Framework)
  - shared interfaces and common types
- [`opensim-source/OpenSim/Capabilities`](/home/rene/vibestorm/opensim-source/OpenSim/Capabilities)
  - CAPS/HTTP endpoints
- [`opensim-source/OpenSim/Services`](/home/rene/vibestorm/opensim-source/OpenSim/Services)
  - backend/grid services
- [`opensim-source/OpenSim/Tests`](/home/rene/vibestorm/opensim-source/OpenSim/Tests)
  - tests and mock clients

## Current Protocol Questions And Where To Look

### Why does a session only receive terse avatar updates?

Start with:

- [`opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs)
- [`opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
- [`opensim-source/OpenSim/Region/Framework/Scenes/Scene.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/Scene.cs)

Relevant anchors:

- `SendInitialData()`
- `SendOtherAgentsAvatarFullToMe()`
- `SendAvatarDataToAgent()`
- `HandleAgentUpdate()`
- update dequeue / prioritization / terse selection around `objectUpdates` and `terseUpdates`

### What does the client need to send to stop being a cloud / Ruth?

Start with:

- [`opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
- [`opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs)
- [`opensim-source/OpenSim/Framework/IClientAPI.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Framework/IClientAPI.cs)

Relevant anchors:

- `HandlerAgentWearablesRequest()`
- `HandlerAgentSetAppearance()`
- `SendWearables()`
- `SendAppearance()`
- `SendAppearanceToAgentNF()`

### Is object delivery pull-based or push-based?

Start with:

- [`opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs)
- [`opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`](/home/rene/vibestorm/opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)

Current source-backed reading:

- the client does influence delivery with `AgentUpdate` camera and `Far` values
- the client also sets bandwidth/throttle via `AgentThrottle`
- but scene/object delivery is still primarily server-driven after session setup
- `SendInitialData()` explicitly sends layer data, land info, avatar data, and object updates once
  prerequisites are met
- later delivery is then shaped by server-side interest management, reprioritization, and culling

So the model is not “client explicitly fetches each object.” It is closer to:

1. client completes session setup and capability bootstrap
2. client sends movement/camera/throttle/appearance signals
3. server decides what to push, and in which update family

## Quick Search Strategy

Prefer targeted searches from a concrete question:

- `rg -n "HandleAgentUpdate|HandleAgentThrottle|CreateImprovedTerseBlock|SendKillObject" opensim-source/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs`
- `rg -n "SendInitialData|SendOtherAgentsAvatarFullToMe|SendAppearanceToAgentNF" opensim-source/OpenSim/Region/Framework/Scenes/ScenePresence.cs`
- `rg -n "InterestManagement|ObjectsCullingByDistance|UpdatePrioritizationScheme" opensim-source/OpenSim/Region/Framework/Scenes/Scene.cs`

Avoid broad repo-wide mining unless the current question genuinely leaves the UDP/session area.
