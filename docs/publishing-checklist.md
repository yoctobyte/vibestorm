# Publishing Checklist

Last checked: 2026-05-10

## Secret Hygiene

- Do not commit `.env` files, local databases, packet captures, or scratch
  scripts.
- `run.sh` may prompt for login details and store them in ignored
  `local/vibestorm-login.env` with mode `600`; do not commit that file.
- Trivial localhost/OpenSim test credentials may be committed when they are
  clearly documented as local-only fixtures and cannot authenticate against
  OSgrid, Second Life, GitHub, hosting providers, or any other real service.
- Do not commit real grid credentials, API tokens, SSH/private keys, OAuth
  tokens, cloud credentials, or reusable passwords for non-local services.
- Local OpenSim docs may record local-only test credentials when needed for
  repeatable tests, but should label them as disposable localhost credentials.

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
