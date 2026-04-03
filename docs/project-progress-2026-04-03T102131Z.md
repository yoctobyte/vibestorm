# Vibestorm Project Progress

Timestamp: 2026-04-03T10:21:31Z

## Summary

Live `ObjectUpdate` fixtures were captured and used to replace guesswork with a known-good decode path:

- known single-prim `ObjectUpdate` packets now decode durable position
- known avatar-style variants no longer crash the session and now decode basic identity fields
- fixture capture can now run in smart mode and only keep anomalous packets

## What The Live Capture Proved

- the stable prim/object packets seen during the run were single-object `ObjectUpdate` messages
- those prim packets used a 60-byte `ObjectData` payload
- the first 12 bytes of that payload are world position floats
- at least one later float group in that payload likely contains rotation/quaternion data
- avatar-style `ObjectUpdate` traffic uses a different variant and includes name-value style text

## Implemented From That

- `ObjectUpdate` parsing now recognizes the known prim variant instead of attempting a full speculative tail walk
- parsed prim objects now include:
  - UUID
  - local ID
  - parent ID
  - pcode
  - scale
  - update flags
  - world position
- parsed avatar-style object updates now include:
  - world position
  - `FirstName`
  - `LastName`
  - `Title`
- unsupported variants fall back to summary-only handling
- smart capture skips known-good packets and writes only anomalous/assumption-violating ones unless capture mode is set to `all`

## Remaining Gaps

- texture/material asset references are not yet extracted from `TextureEntry`
- extra params are not yet decoded
- richer avatar-style `ObjectUpdate` appearance/state is not yet decoded beyond basic identity/name values
- chat/event capture is still separate from `ObjectUpdate` work

## Verification

- `PYTHONPATH=src python -m unittest discover -s test -v`
- `python -m compileall src test`
