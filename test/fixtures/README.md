# Test Fixtures

This directory holds captured or synthetic protocol fixtures.

Rules:

- prefer plain files
- annotate provenance and redactions
- do not commit secrets
- keep fixture names specific to the subsystem they validate

Live session capture convention:

- inbound message bodies may be stored as `<message>/<nnn>-seqXXXXXX.body.bin`
- sidecar metadata may be stored as `<message>/<nnn>-seqXXXXXX.json`
- prefer focused captures such as `ObjectUpdate` over indiscriminate packet dumps
