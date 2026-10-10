# Migration validation — 2026-10-10

Inspected the original Jenkinsfile, Dockerfile, downstream `sqm-autorate-rust`
workflows and package Makefile, Terraform runner definition, and live TheBeast
runner configuration before changing the repository.

## Passed

- YAML parsing; TOML parsing; `sh -n`; ShellCheck; `git diff --check`.
- actionlint 1.7.12, excluding only its unsupported `builtin:checkout` action
  syntax. The workflow uses the existing `ubuntu-latest` label. Gitea's documentation and the
  existing downstream workflow confirm built-in checkout support.
- Default matrix resolves six entries for stable 25.12.5 and oldstable 24.10.8.
  `targets=all` ran upstream target discovery inside the actual job image and
  resolved 194 entries. Empty/invalid targets fail. Every default base-image
  tag was read successfully from GHCR.
- Docker Compose configuration accepted; Redis healthy with its named volume,
  AOF enabled and successful AOF writes. Service and bounded builder were
  provisioned on TheBeast without changing the runner configuration.
- Builder inspected: eight-CPU quota, 20 GiB memory/swap limit, one BuildKit
  worker step, 50 GiB layer GC budget. A second disposable job container with
  fresh Docker client configuration reused the existing daemon and builder.
- Dockerfile build checks completed. Two expected advisory warnings: mandatory
  base-image arguments have no defaults, and Docker's secret-name heuristic
  mistakes the public `SCCACHE_REDIS_KEY_PREFIX` for a secret.
- `configured` Dockerfile stage built for x86/64 on both OpenWrt releases.
  Pinned feeds select Rust 1.94.0 on 25.12.5 and 1.90.0 on 24.10.8. Both retain
  `CONFIG_RUST_SCCACHE=y` through defconfig and contain the explicit bootstrap
  `build.ccache=sccache` setting with LLVM CI downloads disabled.
- Actual OpenWrt make-variable expansion injects sccache into host and package
  compilation, despite unexporting an inherited wrapper.
- Real Cargo library compilation in Docker BuildKit: one Redis miss. A fresh
  runtime container using the Docker-network endpoint: one hit, zero misses.
- Recreated the newly provisioned Redis container using the same named volume;
  a fresh compiler container still recorded one hit, zero misses. No existing
  infrastructure containers were restarted.
- Same source/cache namespace with GNU versus musl target: first musl build
  missed; repetition hit. Rust 1.90.0 versus 1.91.0: first 1.91 build missed;
  repetition hit. All probes reported zero cache read/write errors.
- A disposable OpenWrt package used the real `Build/Compile/Cargo` helper,
  SDK GCC/musl linker, OpenWrt LTO/optimization flags and a local Rust library
  dependency. It built successfully in two separate containers: one dependency
  miss, then one hit. The linked executable correctly remained non-cacheable.
  This used a prebuilt Rust 1.90 toolchain for the probe, not a freshly compiled
  SDK toolchain; it is not a full SDK/source-bootstrap acceptance result.
- Retained `ci/verify-cache.sh` passed its separate-container miss/hit checks
  against that prebuilt validation toolchain. Its original hardcoded SDK rustc
  path was later corrected after the live mediatek/filogic build exposed the
  target-specific staging layout; see the follow-up below.
- Publication configuration checked: original Docker Hub namespace/tag format,
  verification before push, explicit publish input, temporary credential
  directory, no automatic publication trigger. No registry login or push was
  attempted.

## Remaining acceptance

1. Enable repository Actions and configure the two Docker Hub secrets. No
   runner configuration changes are required.
2. Complete a non-publishing Gitea dispatch for the full default matrix.
   The mediatek/filogic 24.10.8 job completed Rust source bootstrap and image
   export; its corrected verification passed on the built image. Acceptance
   for the other combinations remains pending.
3. With explicit publication approval, run the publishing dispatch, pull the
   resulting tags and build the downstream OpenWrt package with the new image.
   Actual Docker Hub authorization and publication remain unverified.
4. After acceptance, disable the old Jenkins job and remove `Jenkinsfile`.
   It is deliberately retained until the replacement passes these checks.

Remote test logs and disposable probe sources are under
`/tmp/openwrt-sdk-rust-migration` in TheBeast's `gitea-runner` instance. The
Redis volume/service and BuildKit builder remain available for the replacement
pipeline; no cache volume or existing infrastructure was deleted.

## Follow-up: mediatek/filogic 24.10.8 verification

The supplied job 1602 log shows full SDK compilation and image export completed
successfully. Verification failed with exit 127 because it assumed
`staging_dir/host/bin/rustc`. Inspection of that exact built image reproduced
the failure and located Rust/Cargo in
`staging_dir/target-aarch64_cortex-a53_musl/host/bin` instead. Both the workflow
and retained cache probe now resolve the unique executable compiler from the
SDK staging tree, rather than assuming a shared host directory.

Reran the corrected verification on that image: Rust 1.90.0 and Cargo 1.90.0
execute successfully. The retained cache probe reports one Redis miss in its
first container and one hit in a fresh second container, with zero cache
read/write errors. ShellCheck, actionlint (excluding only Gitea's built-in
checkout syntax), and `git diff --check` pass. No rebuild or publication was
performed for this fix.

## Follow-up: mediatek/filogic 25.12.5 bootstrap concurrency

Job 1650 failed inside LLVM compilation with
`c++: fatal error: Killed signal terminated program cc1plus`. Its diagnostic
Make retry still configured `LLVM_PARALLEL_COMPILE_JOBS=32`. The persistent
builder's cgroup reports three OOM kills and its memory limit remains 20 GiB;
the counter is cumulative, while the supplied log identifies the killed
compiler in this job. Outer Make parallelism had not bounded Rust bootstrap's
independent job count.

The SDK Rust Makefile now passes `RUST_BUILD_JOBS` to `x.py --jobs`, defaulting
to eight for subsequent SDK use. `build-rust` supplies eight (or the requested
`JOBS`) on the first attempt and one on the serial diagnostic retry. Rust's
bootstrap uses this job count for LLVM's compile pool and CMake build command.

Built the corrected `configured` image for mediatek/filogic 25.12.5, and
expanded the actual OpenWrt `Host/Compile` recipe at both job counts: it invokes
`x.py --jobs=8` and `x.py --jobs=1` respectively. The same recipe checks pass
against the 24.10.8 feed in a disposable container. ShellCheck, shell syntax,
actionlint and diff checks pass. Full source compilation after this change has
not been rerun, and no images were published or infrastructure limits changed.
