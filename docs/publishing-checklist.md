# Publishing Checklist

Last checked: 2026-05-10

## Secret Hygiene

- Do not commit `.env` files, local databases, packet captures, or scratch
  scripts.
- `run.sh` intentionally requires `VIBESTORM_PASSWORD` for login commands and
  does not contain a default password.
- Local OpenSim docs use `VIBESTORM_PASSWORD` placeholders rather than recording
  a reusable local test password.

Useful checks:

```bash
git status --short
git ls-files | rg '(^local/|^opensim-source/|\.sqlite3$|\.db$|\.pcap$|\.pem$|\.key$|\.env|scratch|dump_te|test_transfer)'
git grep -n -I 'changeme123' -- ':!test/*' ':!referencedocs/*' ':!third_party/*'
git grep -n -I -E '(ghp_|github_pat_|sk-[A-Za-z0-9]|xox[baprs]-|AKIA[0-9A-Z]{16}|-----BEGIN (RSA|DSA|EC|OPENSSH|PRIVATE) KEY-----|Authorization: Bearer|client_secret|refresh_token|access_token)' -- ':!referencedocs/*' ':!third_party/*' ':!uv.lock'
```

## Publication Caveats

- Choose and add a top-level project license before presenting Vibestorm as
  open source.
- Review bundled reference material before publication:
  - `referencedocs/` contains OpenSim source excerpts with BSD-style headers.
  - `third_party/secondlife/llviewerregion.cpp` is LGPL 2.1-only viewer source.
  - `third_party/secondlife/message_template.msg` and related artifacts should
    retain provenance and any applicable upstream license/notice material.
- If the public repo should avoid license complexity, remove or replace bundled
  source excerpts with fetch scripts plus notes before publishing.
