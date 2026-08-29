# Runtime Platform Risk: .NET, Mono, and the OpenSim Host

This file exists so we stop re-litigating this. Vibestorm is a Python viewer;
the only reason .NET is anywhere near this project is that our test sim,
OpenSim, is a C# application. This is a record of what that costs us, why we
treat the runtime as untrusted infrastructure rather than a stable dependency,
and what we do about it. Read it once, then go back to protocol work.

None of this is protocol knowledge. It never crosses into
`virtual-world-protocol/`.

## The Situation, Concretely

- Our sim is pinned to `LastDotNetBuild.zip`, release `r575abd6`
  (`LastDotNetAutoBuild`, published 2026-03-29), unpacked at
  `local/opensim/runtime/`.
- It is a **framework-dependent `net8.0`** build. It bundles no runtime:
  `OpenSim.runtimeconfig.json` asks for `Microsoft.NETCore.App 8.0.0`, and
  `opensim-source/runprebuild.sh` builds with
  `/targetframework net8_0`.
- Ubuntu 24.04 shipped `dotnet-runtime-8.0` in its own repos. **Ubuntu 26.04
  does not** — it carries only .NET 10. So the same OpenSim that was a
  two-package apt install on 24.04 has no distro-supported runtime on 26.04.
- .NET 8 reaches end of life in **November 2026**.

The upshot: the only runtime this codebase can correctly run on is one that
Ubuntu has already dropped and Microsoft is about to stop supporting.

## Why We Do Not Trust This Platform

Four failures, each with evidence in this repo.

### 1. Breaking removals inside a still-supported product line

`BinaryFormatter` was not deprecated-then-kept. It was **removed** in .NET 9.
OpenSim still depends on it in four places:

| File | What breaks |
|---|---|
| `OpenSim/Region/CoreModules/Asset/FlotsamAssetCache.cs:529,1015` | on-disk asset cache |
| `OpenSim/Region/Framework/Scenes/KeyframeMotion.cs:317,839` | keyframed prim motion |
| `OpenSim/Region/ScriptEngine/YEngine/XMRInstAbstract.cs` | script state persistence |
| `OpenSim/Framework/Util.cs:2438,2455` | deep-copy helpers |

The documented escape hatch,
`<EnableUnsafeBinaryFormatterSerialization>true</EnableUnsafeBinaryFormatterSerialization>`
— which OpenSim's own Prebuild emits at
`Prebuild/src/Core/Targets/VSGenericTarget.cs:695` — exists only through .NET 8.
In 9+ the type is gone, so the switch is a no-op that silently does nothing.

We measured this. Running our pinned OpenSim on .NET 10 with
`DOTNET_ROLL_FORWARD=LatestMajor` **boots and reports the region ready**, then
fails every asset-cache read:

```
[FLOTSAM ASSET CACHE]: Failed to get file .../assetcache/54f/54f6959b-... :
BinaryFormatter serialization and deserialization have been removed.
```

A sim that starts cleanly and then misbehaves on asset delivery and keyframed
motion is the worst possible failure mode for us, because those are exactly the
paths we capture and decode. It would have quietly poisoned fixtures.

### 2. Cross-platform support retracted after adoption

`System.Drawing.Common` was made Windows-only after .NET 6, and the opt-out
config switch was removed in .NET 7. OpenSim's answer is to ship its own forked
binaries and swap one in at build time:

```
opensim-source/bin/System.Drawing.Common.dll.linux
opensim-source/bin/System.Drawing.Common.dll.win
```

with `runprebuild.sh` doing
`cp bin/System.Drawing.Common.dll.linux bin/System.Drawing.Common.dll`.

A mature project carrying a private fork of a core framework assembly is not a
sign of a healthy platform contract. It is a workaround for a vendor
withdrawing a capability that portable code had already been written against.

### 3. The independent implementation was absorbed, then let go

Mono was the reason C# was ever a credible choice for cross-platform server
software, and it is why OpenSim exists in this form at all. Microsoft acquired
Xamarin in 2016, and in 2024 handed the Mono runtime to the WineHQ project.
Ubuntu 26.04 no longer offers `mono-complete` at all.

So the escape route — "if the vendor runtime moves, run it on the independent
one" — is closed. Mono cannot execute a `net8.0` assembly regardless; it
implements .NET Framework 4.8. Once upstream OpenSim moved to .NET, Mono stopped
being an option, and now it is barely a package.

### 4. A cadence no distribution can carry

Annual releases, three-year LTS. A distro shipped every five years with ten
years of support cannot host that: Ubuntu would be committing to a runtime that
dies a third of the way into the release's life. Dropping .NET 8 from 26.04 is
not an oversight or hostility, it is the only sane packaging decision — which is
precisely the point. The cadence is incompatible with how stable systems are
actually built and shipped.

## Why C# Failed Despite Being a Good Language

The language is genuinely excellent, and saying so is not a concession. Good
generics, real value types, `async`/`await` before most of its peers, pattern
matching, an outstanding profiler story. As a language it beats most of what we
work in.

What failed was never the language. It was the **contract around the runtime**:

- **One vendor owns the runtime, the cadence, and the deprecations.** There is
  no second implementation with standing to say no, and no committee slowing a
  removal down. `BinaryFormatter` went away because one organization decided it
  should.
- **Portability was a product decision, not a property.** It was granted with
  .NET Core, then partially withdrawn (`System.Drawing.Common`). Code written
  against the documented behavior of a "cross-platform" framework aged out
  anyway.
- **Support windows are shorter than real software lifetimes.** OpenSim is 20+
  years old and still under development. It has outlived .NET Framework, Mono's
  independence, and now .NET 8. A runtime that guarantees three years is a poor
  host for software measured in decades.
- **The consequence is stranding.** OpenSim cannot move to .NET 9+ without
  rewriting four subsystems' serialization, and cannot stay on .NET 8 past
  November 2026 without running unsupported. That is not a bad codebase; that is
  what happens when a good language is welded to a runtime with a shorter
  half-life than its users' projects.

Contrast, without smugness: the viewer is Python, and our other hard dependency
is the wire protocol itself. Both are old, boring, and still work. That is the
property we are actually optimizing for.

## What This Costs Us, And What We Do

The cost is bounded, which is why this file ends here rather than in a rewrite
plan. We need a `net8.0` runtime on the machine. It is 31 MB, it installs to
`~/.dotnet` without sudo, and `tools/start_opensim.sh` finds it automatically.
Setup is in `local-opensim.md`.

Rules we hold to:

- **Never roll forward past .NET 8** for the sim. It appears to work and is not
  correct. `tools/start_opensim.sh` deliberately pins `DOTNET_ROOT` rather than
  letting the host resolver pick whatever is installed.
- **The runtime is not a dependency of Vibestorm.** Nothing in `src/` or `test/`
  requires .NET. If the sim host becomes unworkable, we lose a convenient test
  target, not the project.
- **Do not port, patch, or fork OpenSim to chase this.** Fixing
  `BinaryFormatter` is upstream's work. Our job is the viewer.

## What Would Change This Assessment

- Upstream OpenSim replacing `BinaryFormatter` and targeting a current .NET.
  Then we follow their pin and this whole file becomes history.
- .NET 8 going EOL (November 2026) while we still need it. At that point the
  honest options are a pinned 24.04 container (`podman`, `--network=host`) or
  accepting an unsupported runtime on a local-only test host. The container is
  the better answer and is the natural next step if this ever bites again.
