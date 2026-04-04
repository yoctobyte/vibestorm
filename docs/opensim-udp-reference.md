# OpenSim UDP Reference

Date: 2026-04-04

This document is derived from the local OpenSim source files under [`referencedocs/UDP`](/home/rene/vibestorm/referencedocs/UDP).

Scope:

- UDP transport only
- packet families and field layouts that are directly visible in the referenced source
- no attempt to cover CAPS, assets, inventory, or HTTP flows here

This is a source-derived reference, not a speculation document.

Use:

- [`reverse-engineered-protocol.md`](/home/rene/vibestorm/docs/reverse-engineered-protocol.md) for the mixed capture-based working spec
- [`protocol-hypothesis.md`](/home/rene/vibestorm/docs/protocol-hypothesis.md) for the compressed model

## Source Basis

Primary files inspected:

- [`referencedocs/UDP/LLUDPServer.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPServer.cs)
- [`referencedocs/UDP/LLUDPClient.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPClient.cs)
- [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs)
- [`referencedocs/UDP/OpenSimUDPBase.cs`](/home/rene/vibestorm/referencedocs/UDP/OpenSimUDPBase.cs)

## Transport

### UDP Packet Header

The OpenSim UDP path uses the standard LLUDP transport framing:

```text
struct UdpPacket {
    0x00  U8   flags;
    0x01  U32  sequence_be;
    0x05  U8   extra_header_length;
    0x06  U8[] message;
}
```

Observed from source:

- outgoing headers are prebuilt with bytes for `flags`, `sequence`, `extra`, and message number prefix
- `LLUDPServer.SendPacketFinal()` writes the packet sequence into bytes `1..4`
- `LLUDPServer.SendPacketData()` optionally zerocodes the payload before send
- reliable packets are added to `LLUDPClient.NeedAcks`
- received reliable packet sequences are tracked in `LLUDPClient.PacketArchive`

Relevant code:

- [`referencedocs/UDP/LLUDPServer.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPServer.cs)
- [`referencedocs/UDP/LLUDPClient.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPClient.cs)
- [`referencedocs/UDP/OpenSimUDPBase.cs`](/home/rene/vibestorm/referencedocs/UDP/OpenSimUDPBase.cs)

### `PacketAck`

Built explicitly in [`referencedocs/UDP/LLUDPServer.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPServer.cs):

```text
struct PacketAck {
    0x00  U8    flags;                 // 0 in current builder
    0x01  U32   sequence_be;
    0x05  U8    extra_header_length;   // 0
    0x06  U32   message_id_be;         // 0xFFFFFFFB
    0x0A  U8    ack_count;
    0x0B  U32[] ack_ids_le;
}
```

Notes:

- the code batches up to 255 ACK ids per packet
- ACK ids are written little-endian in the message body

### `StartPingCheck`

Built explicitly in [`referencedocs/UDP/LLUDPServer.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPServer.cs):

```text
struct StartPingCheck {
    0x00  U8   flags;                 // 0 in current builder
    0x01  U32  sequence_be;
    0x05  U8   extra_header_length;   // 0
    0x06  U8   message_id;            // 1
    0x07  U8   ping_id;
    0x08  U32  oldest_unacked_le;
}
```

Notes:

- `oldest_unacked_le` comes from `LLUDPClient.NeedAcks.Oldest()`

### `UseCircuitCode`

This packet is handled in [`referencedocs/UDP/LLUDPServer.cs`](/home/rene/vibestorm/referencedocs/UDP/LLUDPServer.cs) via `UseCircuitCodePacket`.

Visible fields from handler usage:

```text
struct UseCircuitCode.CircuitCode {
    U32   code;
    UUID  agent_id;
    UUID  session_id;
}
```

Observed usage:

- authenticated with `AuthenticateSession(session_id, agent_id, code)`
- used to create the `LLUDPClient`

## Handshake / Session Messages

### `RegionHandshake`

Built manually in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct RegionHandshake {
    U32   region_flags;
    U8    sim_access;
    U8    sim_name_len;
    U8[]  sim_name;
    UUID  sim_owner;
    U8    is_estate_manager;
    F32   water_height;
    F32   billable_factor;
    UUID  cache_id;
    UUID  terrain_base_0;     // currently zero UUID
    UUID  terrain_base_1;     // currently zero UUID
    UUID  terrain_base_2;     // currently zero UUID
    UUID  terrain_base_3;     // currently zero UUID
    UUID  terrain_detail_0;
    UUID  terrain_detail_1;
    UUID  terrain_detail_2;
    UUID  terrain_detail_3;
    F32   terrain_start_height_00;
    F32   terrain_start_height_01;
    F32   terrain_start_height_10;
    F32   terrain_start_height_11;
    F32   terrain_height_range_00;
    F32   terrain_height_range_01;
    F32   terrain_height_range_10;
    F32   terrain_height_range_11;
    UUID  region_id;
    I32   cpu_class_id;       // current builder uses 9
    I32   cpu_ratio;          // current builder uses 1
    U8    colo_name_len;      // empty
    U8    product_sku_len;    // empty
    U8    product_name_len;
    U8[]  product_name;
    U8    region_flags_extended_count;
    U64   region_flags_extended[region_flags_extended_count];
    U64   region_protocols;   // current builder sets bit 63
}
```

Notes:

- the packet is sent reliable and zerocoded
- current builder sets `region_flags_extended_count = 1`
- the builder currently reuses `region_flags` as the single extended-flags value
- `region_protocols` comment says:
  - bit 0: server-side texture baking
  - bit 63: more than 6 baked textures support

### `AgentMovementComplete`

Built manually in `MoveAgentIntoRegion()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct AgentMovementComplete {
    UUID  agent_id;
    UUID  session_id;
    F32x3 position;
    F32x3 look_at;
    U64   region_handle_le;
    I32   timestamp_le;
    U16   channel_version_len_le;
    U8[]  channel_version;
}
```

Notes:

- sent reliable
- `position` falls back to the stored start position if the supplied position is zero
- `timestamp_le` is `UnixTimeSinceEpoch()`

## Stable World Messages

### `SimulatorViewerTimeMessage`

Built in `SendViewerTime()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct SimulatorViewerTimeMessage {
    U64    usec_since_start;
    U32    sec_per_day;          // current builder uses 14400
    U32    sec_per_year;         // current builder uses 158400
    F32x3  sun_direction;
    F32    sun_phase;
    F32x3  sun_angular_velocity; // current builder uses zero
}
```

Notes:

- sent non-reliable and zerocoded

### `ChatFromSimulator`

Built manually in `SendChatMessage()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct ChatFromSimulator {
    U8    from_name_len;
    U8[]  from_name;             // UTF-8, current builder includes NUL via helper
    UUID  source_id;
    UUID  owner_id;
    U8    source_type;
    U8    chat_type;
    U8    audible;
    F32x3 from_position;
    U16   message_len;
    U8[]  message;               // UTF-8, current builder uses StringToBytes1024
}
```

Notes:

- sent reliable
- the current source writes `from_name_len` at byte 10 after the 10-byte low-frequency header
- this lines up with the Vibestorm parser trimming trailing NUL bytes from names and messages

### `CoarseLocationUpdate`

Built manually in `SendCoarseLocationUpdate()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct CoarseLocationUpdate {
    U8     location_count;
    U8x3[] locations[location_count];   // x, y, z_quarter_meters
    I16    self_index;
    I16    prey_index;
    U8     agent_count;
    UUID[] agent_ids[agent_count];
}
```

Notes:

- locations are capped at 60
- agent ids are capped at 60
- `z` is quantized as `byte(z * 0.25f)` and clipped to zero if above 1024
- `self_index` is the sender avatar’s index in the location list

### `SimStats`

Built manually in `SendSimStats()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct SimStats {
    U32   region_x;
    U32   region_y;
    U32   region_flags;
    U32   object_capacity;
    U8    stat_count;
    struct StatEntry {
        U32 stat_id;
        F32 stat_value;
    } stats[stat_count];
    I32   pid;                    // current builder uses 0
    U8    region_info_count;      // current builder uses 0
    U64[] region_info;
}
```

Notes:

- the source writes `StatsIndex.ViewerArraySize` entries
- unacked bytes are written in KB instead of bytes
- no extended region info is currently sent

### `LayerData`

Built manually in `SendLayerData(int[] map)` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct LayerData {
    U8   layer_type;
    U16  data_block_size;
    U8[] patch_bitstream;
}
```

Observed source behavior:

- packet header uses high-frequency message id `11`
- the payload uses a `BitPack` writer starting at byte 10
- the bitstream begins with:
  - `STRIDE` packed as 16 bits, current source constant `264`
  - a byte value `16`
  - the `layer_type`
- each terrain patch is appended by `OpenSimTerrainCompressor.CreatePatchFromTerrainData(...)`
- the patch stream terminates with byte value `97` (`END_OF_PATCHES`)
- packets are split when the bitstream grows beyond about 900 bytes

This is enough to treat `LayerData` as a packed terrain-patch stream, though not yet enough to document every inner patch field here.

## Object Lifecycle Messages

### `KillObject`

Built explicitly in `SendKillObject(List<uint> localIDs)` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct KillObject {
    struct ObjectData {
        U32 id;
    } object_data[count];
}
```

Notes from source:

- the server batches up to 200 local IDs per packet
- pending entity property/update queues are cleared before kill send
- source comments elsewhere in `LLClientView` show a kill record is kept to avoid sending late updates after a kill

### `ObjectPropertiesFamily`

Built in `CreateObjectPropertiesFamilyBlock()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs):

```text
struct ObjectPropertiesFamily {
    U32   request_flags;
    UUID  object_id;
    UUID  owner_id_or_zero;
    UUID  group_id;
    U32   base_mask;
    U32   owner_mask;
    U32   group_mask;
    U32   everyone_mask;
    U32   next_owner_mask;
    I32   ownership_cost;        // current source writes zero
    U8    sale_type;
    I32   sale_price;
    U32   category;
    UUID  last_owner_id;
    U16   name_len;
    U8[]  name;
    U16   description_len;
    U8[]  description;
}
```

Notes:

- owner id is zeroed when `owner_id == group_id`
- this is a useful source-backed reference for later object-property decoding

## Object Update Families

### Send-Side Family Selection

The `ProcessEntityUpdates` path in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs) selects the packet family from `PrimUpdateFlags`.

Observed rule:

```text
canUseImproved iff updateFlags only contain:
    AttachmentPoint |
    Position |
    Rotation |
    Velocity |
    Acceleration |
    AngularVelocity |
    CollisionPlane |
    Textures
```

Implications:

- if the filtered flags fit that mask, OpenSim queues the update into `terseUpdates`
- otherwise avatars go to the rich `ObjectUpdate` path
- non-avatar objects either go to compressed updates or rich object updates depending on `useCompressUpdate`

### `ImprovedTerseObjectUpdate`

The outer packet shape is consistent with prior protocol docs:

```text
struct ImprovedTerseObjectUpdate {
    U64   region_handle;
    U16   time_dilation;
    U8    object_count;
    TerseObjectData objects[object_count];
}
```

The actual inner object payload is directly visible in `CreateImprovedTerseBlock()`, `CreatePartImprovedTerseBlock()`, and `CreateAvatartImprovedTerseBlock()`.

#### Part / Generic Entity Terse Block

```text
struct TersePartData {
    U8    data_size;              // current builder uses 44
    U32   local_id;
    U8    attachment_point_nibble_swapped;
    U8    is_avatar;              // 0 for parts
    F32x3 position;
    U16x3 velocity_q;             // clamped to +/-128
    U16x3 acceleration_q;         // clamped to +/-64
    U16x4 rotation_q;             // normalized quaternion components
    U16x3 angular_velocity_q;     // clamped to +/-64
    U16   texture_block_size;     // 0 if absent
    U32   texture_payload_lenish; // only present when texture included
    U8[]  texture_entry_payload;
}
```

Important detail:

- the texture section is not a plain `U16 len + bytes`
- when texture is present, the builder writes:
  - total length = `texture_len + 4` as `U16`
  - raw `texture_len` again as `U32`
  - then the texture bytes

This is directly visible in the source and should drive further decoder work.

#### Avatar Terse Block

```text
struct TerseAvatarData {
    U8    data_size;              // current builder uses 60
    U32   local_id;
    U8    state;
    U8    is_avatar;              // 1 for avatars
    F32x4 collision_plane;
    F32x3 position;
    U16x3 velocity_q;             // clamped to +/-128
    U16x3 acceleration_q;         // current builder uses zero encoding
    U16x4 rotation_q;             // special z/w-only optimization when not flying and not sitting
    U16x3 angular_velocity_q;     // clamped to +/-64
    U16   texture_block_size;     // current builder uses 0
}
```

Important detail:

- avatar acceleration is currently encoded as the fixed zero pattern
- avatar rotation may be encoded with a special “z/w only” form when the avatar is walking/standing

### `ObjectUpdate`

The rich full-state path is built by `CreatePrimUpdateBlock()` and `CreateAvatarUpdateBlock()`.

#### Rich Prim Update Block

```text
struct ObjectUpdatePrimData {
    U32   local_id;
    U8    state;
    UUID  full_id;
    U32   crc;
    U8    pcode;
    U8    material;
    U8    click_action;
    F32x3 scale;

    U8    object_data_size;       // current builder uses 60
    F32x3 position;
    F32x3 velocity;
    F32x3 acceleration;
    F32x4 rotation;
    F32x3 angular_velocity;

    U32   parent_id;
    U32   update_flags;

    U8    path_curve;
    U8    profile_curve;
    U16   path_begin;
    U16   path_end;
    U8    path_scale_x;
    U8    path_scale_y;
    U8    path_shear_x;
    U8    path_shear_y;
    U8    path_twist;
    U8    path_twist_begin;
    U8    path_radius_offset;
    U8    path_taper_x;
    U8    path_taper_y;
    U8    path_revolutions;
    U8    path_skew;
    U16   profile_begin;
    U16   profile_end;
    U16   profile_hollow;

    U16   texture_entry_len;
    U8[]  texture_entry;

    U8    texture_anim_len;
    U8[]  texture_anim;

    U16   name_value_len;
    U8[]  name_value;

    U16   data_len;
    U8[]  data;

    U16   text_len_and_or_zero;   // source uses short-limited UTF-8 helper
    U8[]  text;
    U32   text_color_argb;        // only when text present

    U16   media_url_len;
    U8[]  media_url;

    U8    particle_system_len;
    U8[]  particle_system;

    U8    extra_params_len;
    U8[]  extra_params;

    UUID  sound_id_or_zero;
    UUID  sound_owner_or_zero;
    F32   sound_gain;             // only if sound active, else zeros
    U8    sound_flags;
    F32   sound_radius;

    U8    joint_type;             // current builder zeros these joint fields
    F32x3 joint_pivot;
    F32x3 joint_axis_or_offset;
}
```

Notes:

- attachments may inject a `NameValue` field like `AttachItemID STRING RW SV ...`
- tree/grass cases use special-case inline encoding rather than the normal prim tail
- mesh-related compatibility fixes are applied before serializing some path/profile fields

#### Rich Avatar Update Block

```text
struct ObjectUpdateAvatarData {
    U32   local_id;
    U8    state;                  // current builder uses 0
    UUID  full_id;
    U32   crc;                    // current builder zeros this
    U8    pcode;                  // Avatar
    U8    material;               // Flesh
    U8    click_action;           // 0
    F32x3 avatar_size;

    U8    object_data_size;       // current builder uses 76
    F32x4 collision_plane;
    F32x3 position;
    F32x3 velocity;
    F32x3 acceleration;           // zeros
    F32x4 rotation;
    F32x3 angular_velocity;       // zeros

    U32   parent_id_or_zero;
    U32   update_flags;           // zeros in current builder

    U8[23] pbs_zeros;
    U16    texture_entry_len;     // zero in current builder
    U8     texture_anim_len;      // zero in current builder

    U16    name_value_len;
    U8[]   name_value;            // FirstName / LastName / Title

    trailing_zero_fields...
}
```

Notes:

- avatar name data is explicitly serialized into `NameValue`
- the builder emits a large zero-filled tail after `NameValue`

### `ObjectUpdateCompressed`

The referenced file contains a `CompressedFlags` enum and a commented-out compressed builder skeleton.

Useful directly documented fields:

```text
enum CompressedFlags : U32 {
    ScratchPad        = 0x001,
    Tree              = 0x002,
    HasText           = 0x004,
    HasParticlesLegacy= 0x008,
    HasSound          = 0x010,
    HasParent         = 0x020,
    TextureAnimation  = 0x040,
    HasAngularVelocity= 0x080,
    HasNameValues     = 0x100,
    MediaURL          = 0x200,
    HasParticlesNew   = 0x400
}
```

Because the builder is commented out in the referenced source, this document does not treat the full compressed object layout as source-confirmed.

### `ObjectUpdateCached`

This family is referenced by name in earlier repo notes and public docs, but the local `referencedocs/UDP` slice inspected here did not include a clear active builder. Leave this packet family to the broader protocol notes until a local source path is extracted.

## Incoming Client Messages

These are not fully re-documented here, but the source clearly shows some field usage worth recording.

### `AgentUpdate`

Handled in `HandleAgentUpdate()` in [`referencedocs/UDP/LLClientView.cs`](/home/rene/vibestorm/referencedocs/UDP/LLClientView.cs).

Fields used directly by the server:

```text
struct AgentUpdate.AgentData {
    U32    control_flags;
    U8     flags;
    U8     state;
    F32    far;
    F32x4  body_rotation;
    F32x4  head_rotation;
    F32x3  camera_center;
    F32x3  camera_at_axis;
    F32x3  camera_up_axis;
}
```

Observed semantics:

- server significance filtering compares control flags, flags, state, draw distance, body rotation, and camera vectors
- sequence numbers are used to suppress stale updates

### `AgentThrottle`

Handled in `HandleAgentThrottle()`:

```text
struct AgentThrottle {
    U8[] throttles;
}
```

Observed semantics:

- the server passes the raw throttle byte block to `LLUDPClient.SetThrottles(...)`

### `ObjectExtraParams`

Handled in `HandleObjectExtraParams()`:

```text
struct ObjectExtraParams.ObjectData {
    U32   object_local_id;
    U16   param_type;
    U8    param_in_use;
    U8[]  param_data;
}
```

Observed semantics:

- the server forwards each block to `OnUpdateExtraParams`
- this is directly relevant to future `ExtraParams` decoding in rich `ObjectUpdate`

## Practical Conclusions

Directly useful conclusions from the source:

- the current OpenSim UDP implementation very clearly distinguishes full rich updates from improved terse updates by `PrimUpdateFlags`
- terse `TextureEntry` inside OpenSim is not encoded as a simple variable-2 string; the server prepends both a total size and a second inner length field
- `KillObject` definitely batches multiple local IDs
- `RegionHandshake`, `SimStats`, `CoarseLocationUpdate`, and `SimulatorViewerTimeMessage` are straightforward enough to treat as documented from source rather than guessed from captures
