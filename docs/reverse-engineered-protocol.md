# Vibestorm Reverse-Engineered Protocol Notes

Date: 2026-04-03

This document is a working reverse-engineering reference for Vibestorm development.

It is intentionally practical rather than polished:

- what packet/message shapes we currently believe are correct
- which fields are decoded confidently
- which fields are only partially understood
- which variable-length payloads still need correlation against live OpenSim fixtures

This should be the document to keep open while reading captures, adding decoders, or comparing object variants.

## External Reference Clues

These references are useful for triangulating the protocol, but they are not equally authoritative.

Working rule:

- use live captures and repo tests for byte-accurate trust
- use SL/OpenSim public docs to confirm message families, terminology, and likely semantics
- treat old wiki pages and message-template material as structurally useful but potentially stale

Recent external findings that matter for current work:

- the Second Life protocol index still documents the UDP message system, packet layout, circuits, and packet accounting
  - source: [Protocol](https://wiki.secondlife.com/wiki/Protocol)
  - source: [Packet Layout](https://wiki.secondlife.com/wiki/Packet_Layout)
  - source: [Packet Accounting](https://wiki.secondlife.com/wiki/Packet_Accounting)
  - source: [Circuit](https://wiki.secondlife.com/wiki/Circuit)
- the SL wiki confirms the outer message layouts for the object-update families we care about:
  - `ObjectUpdate`
  - `ImprovedTerseObjectUpdate`
  - `ObjectUpdateCached`
  - `ObjectUpdateCompressed`
  - source: [ObjectUpdate](https://wiki.secondlife.com/wiki/ObjectUpdate)
  - source: [ImprovedTerseObjectUpdate](https://wiki.secondlife.com/wiki/ImprovedTerseObjectUpdate)
  - source: [ObjectUpdateCached](https://wiki.secondlife.com/wiki/ObjectUpdateCached)
  - source: [ObjectUpdateCompressed](https://wiki.secondlife.com/wiki/ObjectUpdateCompressed)
- Linden Lab viewer documentation labels `OUT_TERSE_IMPROVED` as a partial update path rather than a full-object creation path
  - source: [Update Type](https://wiki.secondlife.com/wiki/Update_Type)
- Linden Lab culling/interest-list notes explain the broader lifecycle:
  - the client sends camera/frustum information
  - the server maintains an interest list
  - the simulator subscribes the client to objects with full state first
  - later traffic is expected to be mostly deltas/updates
  - unsubscribe traffic is paired with object-removal messages so the viewer can drop objects cleanly
  - source: [Culling](https://wiki.secondlife.com/wiki/Culling)
- OpenSim exposes interest-management controls that can plausibly change which update families appear in a given session:
  - `[InterestManagement] UpdatePrioritizationScheme`
  - `[InterestManagement] ObjectsCullingByDistance`
  - source: [OpenSim.ini](https://opensimulator.org/wiki/Configuration/files/OpenSim/OpenSim.ini)
- OpenSim release notes explicitly mention `ObjectsCullingByDistance` and interest-management work as an experimental area
  - source: [OpenSim 0.9.0.0 release notes](https://opensimulator.dev/wiki/0.9.0.0_Release)
- Linden Lab acknowledged in 2024-2025 that `message_template.msg` had drifted from current behavior and was updated again
  - implication: the template is useful for framing and message numbers, but not sufficient on its own for behavioral truth
  - source: [message_template.msg feedback thread](https://feedback.secondlife.com/server-bugs/p/documentation-message-templatemsg)
- OpenSim mirror source-history snippets for `LLClientView.cs` show concrete send-side behavior:
  - the update queue is explicitly split into `objectUpdates`, `compressedUpdates`, `terseUpdates`, and `terseAgentUpdates`
  - `ObjectsCullingByDistance` and `UpdatePrioritizationScheme` are consulted during update dequeue/prioritization
  - full updates force the `ObjectUpdate` path
  - when the update flags are compatible with improved terse, the server builds `ImprovedTerseObjectUpdatePacket.ObjectDataBlock` values with `CreateImprovedTerseBlock(...)`
  - self-avatar terse updates can be queued separately from general terse updates
  - older and newer mirror snapshots both show compressed-update support as incomplete or disabled in practice in some code paths
  - source: OpenSim mirror `LLClientView.cs` snippets from search results
    - [queue split and send selection](https://git.4creative.net/OpenSim/OpenSimMirror/src/commit/3ee70aac0b41cd28e41f31a679b4ac4d615f46dc/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
    - [newer send selection with `canUseImproved` / `canUseCompressed`](https://git.4creative.net/OpenSim/OpenSimMirror/src/commit/f61e54892f2284b6f89bacf3069467c05b2eea11/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
    - [older conversion mask for improved terse eligibility](https://git.4creative.net/OpenSim/OpenSimMirror/src/commit/8a3958ad048535ad4f8a752cbd71d9114e53a42b/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
- OpenSim mirror source-history snippets also show specific `KillObject` safeguards:
  - `LLClientView` keeps an `m_killRecord`
  - comments state this is to prevent an update being sent after a kill, because some Linden viewers will keep displaying an ownerless phantom object until relog if that race occurs
  - deleted objects can trigger another kill instead of a late update
  - kill IDs are later flushed together with `SendKillObject(m_killRecord)`
  - source: OpenSim mirror `LLClientView.cs` snippets from search results
    - [kill-record comments and flush behavior](https://git.4creative.net/OpenSim/OpenSimMirror/src/commit/3ee70aac0b41cd28e41f31a679b4ac4d615f46dc/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
    - [lock + kill-record race protection](https://git.4creative.net/OpenSim/OpenSimMirror/src/commit/f61e54892f2284b6f89bacf3069467c05b2eea11/OpenSim/Region/ClientStack/Linden/UDP/LLClientView.cs)
- OpenSim source-history also records a specific change that `SendKillObject` was modified to send multiple local IDs in one packet
  - source: [OpenSim source-history snippet](https://gist.github.com/lkalif/544ba3685dca6b67a83e)
- Second Life viewer-side public notes line up with those server safeguards:
  - viewer render metadata labels `OUT_TERSE_IMPROVED` as a partial update
  - release notes mention warnings about unknown local IDs in the `KillObject` handler
  - source: [Update Type](https://wiki.secondlife.com/wiki/Update_Type)
  - source: [Second Life 3.6.2 release notes](https://wiki.secondlife.com/wiki/Release_Notes/Second_Life_Release/3.6.2.278900)

Current synthesis from those references plus live captures:

- `ObjectUpdate` is the rich full-state lane
- `ImprovedTerseObjectUpdate` is a partial-update lane carrying compact per-object state blobs
- `ObjectUpdateCached` is a cache-oriented lane keyed by `local_id` plus `CRC` and `UpdateFlags`
- `ObjectUpdateCompressed` is a dense lane carrying packed bitstream `Data` per object, heavily relying on property packing
- object appearance and disappearance must be interpreted as part of interest-list subscribe/unsubscribe behavior, not only as create/delete events
- OpenSim session-to-session differences in visible update traffic may be caused by interest-management configuration and scene activity, not only by client bugs
- OpenSim server-side logic appears to choose packet families largely from update flags plus policy/culling state, not from one single global mode
- `KillObject` handling is important enough that OpenSim keeps a kill record specifically to prevent post-kill update races

## Additional LLUDP Update Families

### `ObjectUpdateCached`
Structure:
```
{ RegionData Single { RegionHandle U64 } { TimeDilation U16 } }
{ ObjectData Variable { ID U32 } { CRC U32 } { UpdateFlags U32 } }
```
- Each object essentially supplies a local ID, a CRC to confirm the client has local cache integrity, and associated update flags.

### `ObjectUpdateCompressed`
Structure:
```
{ RegionData Single { RegionHandle U64 } { TimeDilation U16 } }
{ ObjectData Variable { UpdateFlags U32 } { Data Variable 2 } }
```
- A significantly heavier packet structure since `Data Variable 2` contains a packed bitstream. Instead of fields, compression logic operates directly on this binary chunk.

### `ObjectPropertiesFamily`
Structure from local OpenSim source:
```
{ ObjectData Single
    { RequestFlags U32 }
    { ObjectID UUID }
    { OwnerID UUID }
    { GroupID UUID }
    { BaseMask U32 }
    { OwnerMask U32 }
    { GroupMask U32 }
    { EveryoneMask U32 }
    { NextOwnerMask U32 }
    { OwnershipCost S32 }
    { SaleType U8 }
    { SalePrice S32 }
    { Category U32 }
    { LastOwnerID UUID }
    { Name ? }
    { Description ? }
}
```
- OpenSim source currently serializes `Name` and `Description` with short-length UTF-8 helpers.
- The public SL message template still labels those fields as `Variable 1`.
- Vibestorm currently parses this family tolerantly and accepts either short-length or byte-length strings.
- Current local implementation uses it to enrich an already-known `WorldObject` with latest object-property metadata keyed by `ObjectID`.

### `ObjectExtraParams`
Current implementation status:
- standalone `ObjectExtraParams` is parsed as:
  - `AgentID UUID`
  - `SessionID UUID`
  - repeated object blocks:
    - `ObjectLocalID U32`
    - `ParamType U16`
    - `ParamInUse BOOL/U8`
    - `ParamSize U32`
    - `ParamData Variable 1`
- rich `ObjectUpdate.ExtraParams` is currently interpreted as an inner count-prefixed blob:
  - `ParamCount U8`
  - repeated entries:
    - `ParamType U16`
    - `ParamSize U32`
    - `ParamData[ParamSize]`
- the rich-object field itself is now read tolerantly as a 2-byte or 1-byte length-prefixed field, because a real captured OpenSim sculpt-like packet used a 2-byte length there
- the standalone packet shape is source-backed by local OpenSim handler usage plus the SL message template
- the inner rich-object blob shape is now backed by a real captured OpenSim sculpt-like packet:
  - outer payload length `0x0018` = 24
  - count `0x01`
  - type `0x0030`
  - size `0x00000011` = 17
  - payload bytes that look like `UUID + 1-byte subtype`
- this strongly suggests a sculpt-related extra-param block in local OpenSim traffic
- Vibestorm currently decodes the rich blob into structured `extra_params_entries` on `ObjectUpdateEntry` when parsing succeeds
- this area still needs more live captures for non-sculpt subtypes such as flexible/light/projector-style cases

## Confidence Scale

- `confirmed`: directly implemented and exercised in local OpenSim or fixture tests
- `inferred`: strongly suggested by protocol artifacts and current captures
- `unknown`: field exists on the wire but meaning or layout is not yet trusted

## Layering

The current client stack has three protocol layers:

1. XML-RPC login bootstrap
2. UDP message-system simulator traffic
3. HTTP capabilities, especially `EventQueueGet`

This document is mostly about layer 2, with the bootstrap values included because they feed the UDP session.

## Login Bootstrap

The login response currently provides the session bootstrap values needed for the first simulator circuit.

Known response fields:

| Field | Meaning | Confidence | Notes |
| --- | --- | --- | --- |
| `agent_id` | agent UUID | `confirmed` | used in outbound session messages |
| `session_id` | session UUID | `confirmed` | used in handshake and steady-state traffic |
| `secure_session_id` | secondary secure session UUID | `confirmed` | parsed and stored, not yet heavily used |
| `circuit_code` | simulator circuit code | `confirmed` | required for `UseCircuitCode` |
| `sim_ip` | simulator IPv4 address | `confirmed` | UDP destination |
| `sim_port` | simulator UDP port | `confirmed` | UDP destination |
| `seed_capability` | capability seed URL | `confirmed` | used to resolve `EventQueueGet` and others |
| `region_x` | region origin X in meters | `confirmed` | note: not grid coordinate, later divided by 256 for grid index |
| `region_y` | region origin Y in meters | `confirmed` | note: not grid coordinate, later divided by 256 for grid index |
| `message` | human-readable login status | `confirmed` | informational |

## UDP Transport Packet Layout

Current fixed header layout:

| Offset | Size | Meaning | Confidence |
| --- | --- | --- | --- |
| `0` | `1` | flags byte | `confirmed` |
| `1` | `4` | big-endian packet sequence | `confirmed` |
| `5` | `1` | extra-header length | `confirmed` |
| `6..` | variable | extra header then message body | `confirmed` |
| end | variable | optional appended ACK list plus count byte | `confirmed` |

Known flag bits:

| Bit | Name | Meaning | Confidence |
| --- | --- | --- | --- |
| `0x80` | zerocode | packet payload is zerocoded | `confirmed` |
| `0x40` | reliable | sender expects reliable ACK handling | `confirmed` |
| `0x20` | resent | resent packet marker | `confirmed` |
| `0x10` | ack | appended ACK trailer present | `confirmed` |

Current transport conclusions:

- one UDP packet carries one message body
- reliable inbound packets are ACKed explicitly
- explicit `PacketAck` messages are implemented
- duplicate reliable packets are detected and semantically ignored
- appended ACK trailers are parsed independently of the message body

### Transport Struct View

```text
struct UdpPacket {
    0x00  U8   flags;
    0x01  U32  sequence_be;
    0x05  U8   extra_header_length;
    0x06  U8[] extra_header[extra_header_length];
    0x..  U8[] message;
    0x..  U32[] appended_acks_be;   // only if flags & 0x10
    0x..  U8   appended_ack_count;  // last byte if flags & 0x10
}
```

Field roles:

- `flags`: zerocode/reliable/resent/ack trailer bits
- `sequence_be`: packet transport sequence, big-endian
- `extra_header_length`: bytes between fixed header and message body
- `message`: one decoded message-system message
- `appended_acks_be`: transport ACK trailer entries

## Message Number Encoding

The message body begins with a variable-width message number prefix resolved via `message_template.msg`.

Known encoding rules:

| Frequency | Encoded width | Encoding rule | Confidence |
| --- | --- | --- | --- |
| `High` | `1` byte | first byte is message number | `confirmed` |
| `Medium` | `2` bytes | starts with `0xFF`, second byte is suffix | `confirmed` |
| `Low` | `4` bytes | starts with `0xFFFF`, full 32-bit number on wire | `confirmed` |
| `Fixed` | `4` bytes | same width as low, reserved high values | `confirmed` |

This part is stable and backed by tests and live dispatch.

## Current Handshake Sequence

Minimum live connection flow:

1. login response yields simulator address, IDs, `circuit_code`, and seed capability
2. client sends `UseCircuitCode`
3. client sends `CompleteAgentMovement`
4. simulator sends `AgentMovementComplete`
5. simulator sends `RegionHandshake`
6. client sends `RegionHandshakeReply`
7. simulator sends periodic `StartPingCheck`
8. client sends `CompletePingCheck`
9. client sends periodic `AgentUpdate`

Observed related messages:

| Message | Direction | Purpose | Confidence |
| --- | --- | --- | --- |
| `UseCircuitCode` | client -> sim | establish UDP circuit | `confirmed` |
| `CompleteAgentMovement` | client -> sim | finish initial movement bootstrap | `confirmed` |
| `AgentMovementComplete` | sim -> client | confirms movement/bootstrap complete | `confirmed` |
| `RegionHandshake` | sim -> client | region metadata | `confirmed` |
| `RegionHandshakeReply` | client -> sim | acknowledge handshake | `confirmed` |
| `StartPingCheck` | sim -> client | health/ping request | `confirmed` |
| `CompletePingCheck` | client -> sim | ping response | `confirmed` |
| `PacketAck` | both | explicit ACK transport message | `confirmed` |
| `AgentThrottle` | client -> sim | viewer bandwidth throttle settings | `confirmed` in code path, lightly interpreted |
| `AgentUpdate` | client -> sim | steady-state movement/camera/control update | `confirmed` |

## Known Message Semantics

### `UseCircuitCode`

Current decoded body:

| Field | Type | Confidence |
| --- | --- | --- |
| `code` | `U32` little-endian | `confirmed` |
| `session_id` | UUID | `confirmed` |
| `agent_id` | UUID | `confirmed` |

```text
struct UseCircuitCode {
    0x00  U32  code_le;
    0x04  UUID session_id;
    0x14  UUID agent_id;
}
```

### `CompleteAgentMovement`

Current outbound builder uses:

| Field | Type | Confidence |
| --- | --- | --- |
| `agent_id` | UUID | `confirmed` |
| `session_id` | UUID | `confirmed` |
| `circuit_code` | `U32` little-endian | `confirmed` |

```text
struct CompleteAgentMovement {
    0x00  UUID agent_id;
    0x10  UUID session_id;
    0x20  U32  circuit_code_le;
}
```

### `AgentMovementComplete`

Current decoded fields:

| Field | Type | Confidence | Notes |
| --- | --- | --- | --- |
| `agent_id` | UUID | `confirmed` | |
| `session_id` | UUID | `confirmed` | |
| `position` | `Vector3` | `confirmed` | world position |
| `look_at` | `Vector3` | `confirmed` | viewing direction |
| `region_handle` | `U64` | `confirmed` | |
| `timestamp` | `U32` | `confirmed` | semantics not deeply used yet |
| `channel_version` | variable string | `confirmed` | length-prefixed |

```text
struct AgentMovementComplete {
    0x00  UUID    agent_id;
    0x10  UUID    session_id;
    0x20  F32x3   position;
    0x2C  F32x3   look_at;
    0x38  U64     region_handle_le;
    0x40  U32     timestamp_le;
    0x44  U16     channel_version_len_le;
    0x46  U8[]    channel_version[channel_version_len];
}
```

### `RegionHandshake`

Current decoded fields:

| Field | Type | Confidence | Notes |
| --- | --- | --- | --- |
| `region_flags` | `U32` | `confirmed` | raw bitfield not fully interpreted |
| `sim_access` | `U8` | `confirmed` | access class value not yet normalized |
| `sim_name` | variable string | `confirmed` | |
| `sim_owner` | UUID | `confirmed` | |
| `is_estate_manager` | bool | `confirmed` | |
| `water_height` | `F32` | `confirmed` | |
| `billable_factor` | `F32` | `confirmed` | |
| `cache_id` | UUID | `confirmed` | |
| `region_id` | UUID | `confirmed` | |

Ignored but present blocks:

- terrain and ground texture UUID arrays
- additional region metadata between `cache_id` and `region_id`

Those blocks exist structurally but are not yet normalized.

```text
struct RegionHandshake {
    0x00  U32     region_flags_le;
    0x04  U8      sim_access;
    0x05  U8      sim_name_len;
    0x06  U8[]    sim_name[sim_name_len];
    0x..  UUID    sim_owner;
    0x..  U8      is_estate_manager;
    0x..  F32     water_height_le;
    0x..  F32     billable_factor_le;
    0x..  UUID    cache_id;
    0x..  U8[128] terrain/base texture block;   // not decoded semantically yet
    0x..  U8[32]  additional region block;      // not decoded semantically yet
    0x..  UUID    region_id;
}
```

### `SimStats`

Current decoded fields:

| Field | Type | Confidence |
| --- | --- | --- |
| `region_x` | `U32` | `confirmed` |
| `region_y` | `U32` | `confirmed` |
| `region_flags` | `U32` | `confirmed` |
| `object_capacity` | `U32` | `confirmed` |
| `stats` | repeated `(stat_id, stat_value)` | `confirmed` structurally |
| `pid` | `S32` | `confirmed` |
| `region_flags_extended` | repeated `U64` | `confirmed` structurally, meanings unknown |

Unknowns:

- semantic labels for individual `stat_id` values
- meaning of most extended region-info values

```text
struct SimStats {
    0x00  U32   region_x_le;
    0x04  U32   region_y_le;
    0x08  U32   region_flags_le;
    0x0C  U32   object_capacity_le;
    0x10  U8    stat_count;
    0x11  Stat  stats[stat_count];
    0x..  S32   pid_le;
    0x..  U8    region_info_count;
    0x..  U64   region_flags_extended[region_info_count];
}

struct Stat {
    0x00  U32   stat_id_le;
    0x04  F32   stat_value_le;
}
```

### `CoarseLocationUpdate`

Current decoded fields:

| Field | Type | Confidence |
| --- | --- | --- |
| `locations` | repeated `(x, y, z)` byte triplets | `confirmed` |
| `you_index` | `S16` | `confirmed` |
| `prey_index` | `S16` | `confirmed` |
| `agent_ids` | repeated UUIDs | `confirmed` |

Interpretation:

- this provides a low-fidelity regional presence list
- coordinates are coarse, not full-precision world transforms

```text
struct CoarseLocationUpdate {
    0x00  U8      location_count;
    0x01  U8x3    locations[location_count];  // x, y, z bytes
    0x..  S16     you_index_le;
    0x..  S16     prey_index_le;
    0x..  U8      agent_count;
    0x..  UUID    agent_ids[agent_count];
}
```

### `SimulatorViewerTimeMessage`

Current decoded fields:

| Field | Type | Confidence |
| --- | --- | --- |
| `usec_since_start` | `U64` | `confirmed` |
| `sec_per_day` | `U32` | `confirmed` |
| `sec_per_year` | `U32` | `confirmed` |
| `sun_direction` | `Vector3` | `confirmed` |
| `sun_phase` | `F32` | `confirmed` |
| `sun_angular_velocity` | `Vector3` | `confirmed` |

```text
struct SimulatorViewerTimeMessage {
    0x00  U64   usec_since_start_le;
    0x08  U32   sec_per_day_le;
    0x0C  U32   sec_per_year_le;
    0x10  F32x3 sun_direction;
    0x1C  F32   sun_phase_le;
    0x20  F32x3 sun_angular_velocity;
}
```

### `ChatFromSimulator`

Current decoded fields:

| Field | Type | Confidence |
| --- | --- | --- |
| `from_name` | variable string | `confirmed` |
| `source_id` | UUID | `confirmed` |
| `owner_id` | UUID | `confirmed` |
| `source_type` | `U8` | `confirmed` |
| `chat_type` | `U8` | `confirmed` |
| `audible` | `U8` | `confirmed` |
| `position` | `Vector3` | `confirmed` |
| `message` | variable UTF-8 string | `confirmed` |

```text
struct ChatFromSimulator {
    0x00  U8      from_name_len;
    0x01  U8[]    from_name[from_name_len];
    0x..  UUID    source_id;
    0x..  UUID    owner_id;
    0x..  U8      source_type;
    0x..  U8      chat_type;
    0x..  U8      audible;
    0x..  F32x3   position;
    0x..  U16     message_len_le;
    0x..  U8[]    message[message_len];
}
```

### `PacketAck`

Current decoded body:

| Field | Type | Confidence |
| --- | --- | --- |
| `count` | `U8` | `confirmed` |
| `packets` | repeated `U32` little-endian | `confirmed` |

```text
struct PacketAck {
    0x00  U8    count;
    0x01  U32   packets_le[count];
}
```

### `ImprovedTerseObjectUpdate`

This is now the most important missing object/world message family in local OpenSim sessions.

Recent live evidence:

- it appeared 60 times in a 120-second local session
- only 3 `ObjectUpdate` packets appeared in the same session
- this strongly suggests `ImprovedTerseObjectUpdate` carries a large share of visible scene state
- the local `message_template.msg` confirms the outer blocks:
  - `RegionData.RegionHandle U64`
  - `RegionData.TimeDilation U16`
  - repeated `ObjectData.Data Variable 1`
  - repeated `ObjectData.TextureEntry Variable 2`
- the semantic layout inside each `Data` payload is now confirmed from OpenSim `LLClientView.cs`
- it branches into two layouts: Avatar (60 bytes) and Prim (44 bytes)
- `TextureEntry` uses a custom 4-byte header (`totlen`, `len`) before the TE payload

Current decoded fields:

| Field | Type | Confidence | Notes |
| --- | --- | --- | --- |
| `region_handle` | `U64` | `confirmed` | |
| `time_dilation` | `U16` | `confirmed` | |
| `object_count` | `U8` | `confirmed` | |
| `data` | variable | `confirmed` | branches by `data_size` (60 or 44) |
| `texture_entry` | variable | `confirmed` | |

```text
struct ImprovedTerseObjectUpdate {
    0x00  U64   region_handle_le;
    0x08  U16   time_dilation_le;
    0x0A  U8    object_count;
    0x0B  TerseEntry entries[object_count];
}

struct TerseEntry {
    0x00  Var1   data;
    0x..  Var2   texture_entry;
}
```

### Terse Data Layout (Avatar)
Size: 60 bytes.

| Offset | Size | Meaning | Confidence |
| --- | --- | --- | --- |
| `0` | `4` | `local_id` (U32 LE) | `confirmed` |
| `4` | `1` | `state` (U8) | `confirmed` |
| `5` | `1` | `is_avatar` (`0x01`) | `confirmed` |
| `6` | `16` | `collision_plane` (Vector4) | `confirmed` |
| `22` | `12` | `position` (Vector3) | `confirmed` |
| `34` | `6` | `velocity` (3x U16) | `confirmed` |
| `40` | `6` | `acceleration` (3x U16) | `confirmed` |
| `46` | `8` | `rotation` (4x U16) | `confirmed` |
| `54` | `6` | `angular_velocity` (3x U16) | `confirmed` |

### Terse Data Layout (Prim)
Size: 44 bytes.

| Offset | Size | Meaning | Confidence |
| --- | --- | --- | --- |
| `0` | `4` | `local_id` (U32 LE) | `confirmed` |
| `4` | `1` | `state` (U8 - attach flags) | `confirmed` |
| `5` | `1` | `is_avatar` (`0x00`) | `confirmed` |
| `6` | `12` | `position` (Vector3) | `confirmed` |
| `18` | `6` | `velocity` (3x U16) | `confirmed` |
| `24` | `6` | `acceleration` (3x U16) | `confirmed` |
| `30` | `8` | `rotation` (4x U16) | `confirmed` |
| `38` | `6` | `angular_velocity` (3x U16) | `confirmed` |

### Terse Vector/Quaternion Compression
OpenSim uses `Utils.FloatToUInt16Bytes` which clamps values to a range (`1.0`, `64.0`, or `128.0`) and maps them to `0..65535`.
- Range `1.0`: `(val + 1.0) * 32767.5`
- Range `64.0`: `(val + 64.0) * 511.9921875`
- Range `128.0`: `(val + 128.0) * 255.99609375`

## `ObjectUpdate`

This is the most important reverse-engineering area right now.

### Current Top-Level Layout

Known current prefix:

| Field | Type | Confidence |
| --- | --- | --- |
| `region_handle` | `U64` | `confirmed` |
| `time_dilation` | `U16` | `confirmed` |
| `object_count` | `U8` | `confirmed` |

Current implementation limitation:

- only `object_count == 1` is currently decoded semantically
- multi-object packets are recognized but not yet fully handled

```text
struct ObjectUpdate {
    0x00  U64   region_handle_le;
    0x08  U16   time_dilation_le;
    0x0A  U8    object_count;
    0x0B  ObjectEntry objects[object_count];   // only single-object path currently decoded
}
```

### Per-Object Header

Current decoded per-object prefix:

| Field | Type | Confidence |
| --- | --- | --- |
| `local_id` | `U32` | `confirmed` |
| `state` | `U8` | `confirmed` |
| `full_id` | UUID | `confirmed` |
| `crc` | `U32` | `confirmed` |
| `pcode` | `U8` | `confirmed` |
| `material` | `U8` | `confirmed` |
| `click_action` | `U8` | `confirmed` |
| `scale` | `Vector3` | `confirmed` |
| `ObjectData` length | `U8` | `confirmed` |
| `ObjectData` payload | variable | `confirmed` structurally |
| `parent_id` | `U32` | `confirmed` |
| `update_flags` | `U32` | `confirmed` as raw bitfield |

After that, the current parser branches by `(pcode, object_data_size)`.

```text
struct ObjectEntry {
    0x00  U32    local_id_le;
    0x04  U8     state;
    0x05  UUID   full_id;
    0x15  U32    crc_le;
    0x19  U8     pcode;
    0x1A  U8     material;
    0x1B  U8     click_action;
    0x1C  F32x3  scale;
    0x28  U8     object_data_len;
    0x29  U8[]   object_data[object_data_len];
    0x..  U32    parent_id_le;
    0x..  U32    update_flags_le;
    0x..  U8[]   variant_tail;
}
```

### Known Variant: Prim Basic

Signature:

- `pcode = 9`
- `ObjectData` size = `60`

Decoded fields:

| Field | Type | Confidence | Notes |
| --- | --- | --- | --- |
| `position` | `Vector3` | `confirmed` | from first 12 bytes of `ObjectData` |
| `rotation` | `Quaternion/F32x4` | `inferred` | from bytes 40..55 of `ObjectData`; orientation is useful, exact semantic trust still moderate |
| `variant` | `"prim_basic"` | `confirmed` | internal label |

```text
struct PrimBasicObjectData {
    0x00  F32x3  position;
    0x0C  U8[28] unknown_block_a;
    0x28  F32x4  rotation_like;
    0x38  U8[4]  unknown_block_b;
}
```

### Known Variant: Avatar Basic

Signature:

- `pcode = 47`
- `ObjectData` size = `76`

Decoded fields:

| Field | Type | Confidence | Notes |
| --- | --- | --- | --- |
| `position` | `Vector3` | `confirmed` | read from bytes 16..27 of `ObjectData` |
| `variant` | `"avatar_basic"` | `confirmed` | internal label |
| `NameValue` | parsed key/value metadata | `confirmed` structurally | currently used for `FirstName`, `LastName`, `Title` |

```text
struct AvatarBasicObjectData {
    0x00  U8[16] unknown_block_a;
    0x10  F32x3  position;
    0x1C  U8[32] unknown_block_b;
    0x3C  F32    unknown_scalar;
    0x40  U8[12] unknown_block_c;
}
```

### Current Tail Layout Assumption

For currently known single-object `ObjectUpdate` variants, the parser expects this tail order after the fixed body:

1. 22 unknown bytes
2. `TextureEntry` as length-prefixed variable field
3. `TextureAnim` as length-prefixed variable field
4. `NameValue` as length-prefixed variable field
5. `Data` as length-prefixed variable field
6. `Text` as length-prefixed variable field
7. 4-byte text color
8. `MediaURL` as length-prefixed variable field
9. `PSBlock` as length-prefixed variable field
10. `ExtraParams` as length-prefixed variable field
11. trailing bytes if any

Confidence:

- field ordering is `inferred` but working against current fixtures
- variable-length parsing is currently “best effort” to tolerate endian/shape uncertainty

```text
struct ObjectUpdateTailAssumption {
    0x00  U8[22] pre_tail_unknown;
    0x16  Var2   texture_entry;
    0x..  Var1   texture_anim;
    0x..  Var2   name_value;
    0x..  Var2   data;
    0x..  Var1   text;
    0x..  U8[4]  text_color_rgba_like;
    0x..  Var1   media_url;
    0x..  Var1   ps_block;
    0x..  Var1   extra_params;
    0x..  U8[]   trailing_bytes;
}

struct Var1 {
    0x00  U8     len;
    0x01  U8[]   payload[len];
}

struct Var2 {
    0x00  U16    len;   // endian still not fully trusted for every field
    0x02  U8[]   payload[len];
}
```

### Variable-Length Field Handling

Current parser behavior:

- some lengths are read with a fixed little-endian rule where known
- several tail fields use best-effort length parsing against both little-endian and big-endian interpretations
- the parser accepts the first interpretation that fits the remaining payload

This is useful for discovery, but it means:

- a successful parse is not always proof that the field-length rule is fully correct
- further fixture correlation is still required before freezing the exact length semantics for every tail field

### `NameValue`

Current `NameValue` handling:

- payload is decoded as text lines
- each line is split into five logical columns
- fields of the form `KEY STRING RW SV VALUE` are promoted to `name_values[KEY] = VALUE`

Known examples:

| Key | Meaning | Confidence |
| --- | --- | --- |
| `FirstName` | avatar first name | `confirmed` |
| `LastName` | avatar last name | `confirmed` |
| `Title` | avatar title | `confirmed` |

Current limitation:

- ordinary prim names from local OpenSim probes have not yet appeared in the decoded `NameValue` field
- object labels like `cube` or `sphere` therefore cannot yet be correlated from `ObjectUpdate` alone

### `TextureEntry`

Current understanding:

- a non-empty `TextureEntry` is a strong signal for a rich prim update
- when at least 16 bytes are present, the first 16 bytes are currently treated as a conservative default texture UUID

Observed live fixture result:

| Field | Observed value |
| --- | --- |
| variant | `prim_basic` |
| `TextureEntry` size | `64` bytes |
| inferred default texture UUID | `00895567-4724-cb43-ed92-0b47caed1546` |

What this proves:

- visual appearance changes are represented inside `ObjectUpdate`
- the first 16 bytes of current 64-byte `TextureEntry` payloads are stable enough to expose a default texture UUID

What remains unknown:

- full `TextureEntry` structure
- per-face overrides
- repeat/offset/rotation values
- material/gloss/normal/specular extensions

### Known Unknown Tail Fields

Current tail fields that are structurally recognized but not semantically decoded:

| Field | Current handling | Confidence | Notes |
| --- | --- | --- | --- |
| 22-byte pre-tail block | **Confirmed** (Path/Profile) | `confirmed` | 18 profile/path parameters |
| `TextureEntry` | size tracked, payload summarized | `confirmed` | |
| `TextureAnim` | size tracked, payload summarized when non-zero | `unknown` | |
| `Data` | size tracked, payload summarized when non-zero | `unknown` | |
| `Text` | size tracked, ASCII preview emitted when non-zero | `inferred` | |
| text color | 4 raw bytes retained when non-zero | `inferred` | |
| `MediaURL` | size tracked, payload summarized when non-zero | `unknown` | |
| `PSBlock` | size tracked, payload summarized when non-zero | `unknown` | |
| `ExtraParams` | size tracked, payload summarized when non-zero | `unknown` | |
| trailing bytes | summarized when non-zero | `unknown` | |

### The 22-byte "Pre-Tail" Block
This block consists of the following 18 fields (mostly U8 and S8) used for path and profile definitions:

1. `PathCurve` (U8)
2. `PathBegin` (U16)
3. `PathEnd` (U16)
4. `PathScaleX` (U8)
5. `PathScaleY` (U8)
6. `PathShearX` (U8)
7. `PathShearY` (U8)
8. `PathTwist` (S8)
9. `PathTwistBegin` (S8)
10. `PathRadiusOffset` (S8)
11. `PathTaperX` (S8)
12. `PathTaperY` (S8)
13. `PathRevolutions` (U8)
14. `PathSkew` (S8)
15. `ProfileCurve` (U8)
16. `ProfileBegin` (U16)
17. `ProfileEnd` (U16)
18. `ProfileHollow` (U16)

Total byte size is exactly 22. (Confirmed).

### Current Live `ObjectUpdate` Evidence

From the latest captured rich prim fixtures:

- all 8 observed rich packets referred to the same object UUID
- all 8 had the same `TextureEntry` size of 64 bytes
- all 8 produced the same inferred default texture UUID
- `TextureAnim`, `ExtraParams`, `MediaURL`, `PSBlock`, and `Text` were empty in those captures

Interpretation:

- repeated capture of the same object state is useful for stability checks
- it is not enough to identify the semantics of unknown tail fields
- future capture sessions must intentionally vary one simulator-side property at a time

## Unknown Collection Strategy

Current collection mechanisms:

- optional body capture under `test/fixtures/live/`
- structured fixture inventory generation
- default SQLite evidence collection at `local/unknowns.sqlite3`

The SQLite database should be treated as the long-lived reverse-engineering evidence store.

Current intended usage:

- run `./run.sh session`
- inspect aggregate unknown summaries with `./run.sh unknowns`
- rebuild file-based fixture inventory with `./run.sh fixtures` when body captures are enabled

## Recommended Probe Matrix

To make unknown fields correlate cleanly, vary one property at a time.

Suggested labeled objects:

- `cube`
- `cube textured`
- `cube hovertext`
- `cube resized`
- `cube rotated`
- `sphere`
- `sphere hovertext`
- `sphere textured`
- `sphere with media`
- `cube with extra params`

For each pair, keep all unrelated properties fixed.

## Open Questions

1. Where do ordinary prim names actually appear on the wire for current OpenSim `ObjectUpdate` traffic?
2. Is the current 22-byte skipped block a stable header extension, and what are its subfields?
3. What is the exact length/endian rule for every `ObjectUpdate` tail field?
4. What are the semantic labels for `update_flags` bits?
5. What does `TextureAnim` look like when explicitly enabled on a prim?
6. Which object features populate `ExtraParams` in local OpenSim?
7. When hover text is enabled, does it arrive in `Text`, `NameValue`, another UDP message, or a capability event?
8. Which additional object update families need decoding next: `ImprovedTerseObjectUpdate`, `ObjectUpdateCached`, `KillObject`?

## How To Use This Document

When a new capture or behavior shows up:

1. add the raw observation here
2. mark whether it is `confirmed`, `inferred`, or still `unknown`
3. include an example value if one is stable
4. note the object/setup that produced it
5. only promote a field to `confirmed` after repeatable fixture evidence

This document should evolve alongside the decoder rather than lag behind it.
