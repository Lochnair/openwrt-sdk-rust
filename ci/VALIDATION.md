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

## Follow-up: runner-owned Redis and SSD cache — 2026-10-11

Initially prototyped a 1 GiB Redis LRU hot cache and Apache WebDAV backed by a
persistent Docker volume on `fastpool`. This prototype was superseded by the
S3 setup below after testing both requested alternatives. The SDK repository
consumes these services and no longer deploys Redis. Production services have
not been redeployed; the original Redis container and volume remain untouched.

Inspected the pinned sccache 0.18.0 source and ran a cross-container central
server experiment. With client-side compilation and an unshared disk backend,
the first compile missed; the second reported a cache error and recompiled,
because `IpcStorage` attempts to open the daemon's returned absolute cache path.
TCP addresses are fixed to loopback in `net::SocketAddr::with_port`. In addition,
client-side `get_raw` bypasses multi-level read statistics and backfilling. The
standard per-job daemon mode with Redis/WebDAV avoids these restrictions.

Writes in standard mode can still be outstanding when the compiler client
returns. `SCCACHE_MULTILEVEL_WRITE_ERROR_POLICY=all` awaits both tiers within a
write; `sccache-stats` then drains writes and backfills for up to 30 seconds before
printing statistics and stopping the job daemon. Its completion checks use the
pinned version's cache-miss/write and per-tier backfill counters.

Passed on TheBeast using an isolated `sccache-probe` Compose project:

- Built the WebDAV image and validated Apache configuration. Real writes exposed
  the base Alpine image's missing APR DBM driver; the added Dockerfile installs
  `apr-util-dbm_gdbm`. Both services then passed health checks and real writes.
- Retained CI verification: one miss writing both tiers, one Redis hit in a
  fresh container, then one WebDAV-only hit in a third fresh container. Zero
  cache errors, read/write errors, or per-tier write failures.
- Real Rust compilation inside Docker BuildKit followed by a fresh SDK runtime
  container: miss, then hit, using host-loopback and Docker-DNS endpoints.
- Repeated the unchanged BuildKit build: the actual compilation RUN step was
  cached and did not invoke sccache again.
- Recreated both test service containers, retaining only the WebDAV volume:
  Redis started empty. A fresh SDK container hit WebDAV and backfilled Redis;
  the following fresh container hit Redis.
- Identical source and prefix with GNU versus musl targets: musl missed, then
  hit. Rust 1.90.0 versus 1.94.0 under the same test prefix: 1.94.0 missed, then
  hit. All cache-error counters remained zero.
- A disposable OpenWrt package compiled via the real `rust-package.mk`
  `Build/Compile/Cargo` helper and SDK musl linker in two fresh SDK containers:
  dependency miss, then hit. This used the actual completed 24.10.8 SDK image,
  not the earlier validation toolchain. The binary remained non-cacheable.
- Proposed full runner Compose configuration, YAML parsing, Apache config,
  ShellCheck, shell syntax, Python syntax, actionlint, and diff checks passed.
  actionlint excludes only Gitea's built-in checkout syntax. Dockerfile checks
  exclude the public `KEY_PREFIX` secret-name heuristic and mandatory base
  image argument warning, as in the original validation.
- Publishing remains manual, with the original image tags and verification
  before registry login/push. No login or publication was performed. Matrix,
  runner labels/capacity, BuildKit caching, and serial verbose retry are unchanged.

Full SDK source bootstrap and a Gitea dispatch were not rerun for this cache
change. Deploy/build the updated runner stack before using the changed workflow.
The prototype WebDAV tier had no automatic LRU or capacity limit; the final
S3 configuration below adds an independent hard filesystem bound and retention.
Old Redis entries are not migrated; retiring the old service/volume is a separate
approved cutover step. Test logs are under `/tmp/gitea-sccache-stack` and
`/tmp/gitea-sccache-tests` on the runner; isolated test services are cleaned up.

Sources: [sccache architecture](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Architecture.md),
[pinned IPC storage](https://github.com/mozilla/sccache/blob/v0.18.0/src/cache/ipc_storage.rs),
[pinned multi-level implementation](https://github.com/mozilla/sccache/blob/v0.18.0/src/cache/multilevel.rs).


## Follow-up: RustFS and VersityGW comparison — 2026-10-11

Selected **RustFS 1.0.1** after testing it and **VersityGW 1.8.0**, following the
owner's preference. The final runner Compose configuration uses Redis/S3 and
removes the uncommitted Apache image/config prototype. Services were tested in
isolated projects, not deployed over the existing runner/cache services.

Both candidates passed real sccache 0.18.0 compilation tests using the completed
SDK images on TheBeast:

- Cold miss wrote both tiers; fresh-container Redis hit; S3-only hit.
- Service restart plus empty isolated Redis: S3 hit backfilled Redis, then the
  next fresh container hit Redis. Cache read/write/error counters stayed zero.
- Same prefix/input with GNU versus musl targets: musl missed then hit. Rust
  1.90.0 versus 1.94.0: the different toolchain missed then hit.

The objects live on a separate Incus ZFS volume with a 16 GiB hard quota for
these tests. A separate 64 MiB quota probe rejected an 80 MiB incompressible write
with `Disk quota exceeded`. No production data was used for the quota test.
Production setup requires a new **100 GiB** quota-backed Incus volume; Compose
requires an existing data directory and rejects a filesystem reporting more than
100 GiB capacity. This guard passed on the quota-backed mount and rejected the
unbounded SSD filesystem. Missing BuildKit credentials failed before compilation,
and test logs contained no credential values. RustFS log files use a bounded tmpfs; Docker logs rotate.

Storage microbenchmark: 64 objects of 1 MiB incompressible data, three passes,
median aggregate MiB/s over host-loopback HTTP on the same SSD pool. Every GET
was compared with its source bytes. These include client/protocol overhead and
warm filesystem cache; they do not measure full SDK build speed or sustained
cold-SSD throughput.

| Backend | PUT, 1 client | GET, 1 client | PUT, 4 clients | GET, 4 clients |
| --- | ---: | ---: | ---: | ---: |
| Apache WebDAV prototype | 110.2 | 660.8 | failed | not run |
| RustFS 1.0.1 | 101.5 | 314.7 | 231.2 | 471.2 |
| VersityGW 1.8.0 | 99.1 | 329.3 | 232.2 | 410.2 |

WebDAV's four-client PUT test returned HTTP 500 lock-database errors, despite
passing sequential compiler-cache tests. Both S3 backends handled that load.
Observed Docker memory usage after the probes was about 202 MiB for RustFS and
28 MiB for VersityGW; these are small test workloads, not worst-case bounds.
RustFS was chosen for preference and compatibility, not a proven performance
advantage over VersityGW. Sequential S3 GETs were slower than the WebDAV baseline.
Redis remains the primary hot tier.

Final RustFS setup and SDK integration checks:

- The entire proposed runner Compose configuration validates with dummy
  registration credentials. Only the cache services ran in the isolated project;
  both passed their real health checks. Bucket initialization with pinned official
  AWS CLI 2.37.12 passed. RustFS accepted and returned the exact configured
  30-day object expiration and one-day abandoned multipart-upload policy.
  Expiration execution itself was not tested by waiting 30 days.
- Replaced the original test RustFS container with the proposed Compose service
  over the same retained quota-backed data: an earlier Rust artifact was still an
  S3 hit and backfilled a new Redis namespace in a fresh SDK container.
- The retained `ci/verify-cache.sh` passed all five probes (cold writes, Redis hit,
  S3-only hit, backfill into a fresh Redis namespace, subsequent Redis hit) on
  x86-64/24.10.8 and mediatek-filogic/25.12.5. It never flushes shared Redis data.
- Real BuildKit Rust compilation using the proposed required secret mounts,
  then a fresh runtime-container cache hit, passed. Secret values were absent
  from exported image configuration/history; subsequent layers and a fresh
  container had no credential environment variables or secret files. The repeated
  unchanged compilation layer was cached. Only a local disposable image was
  produced; no registry login/push occurred.
- Actual OpenWrt `Build/Compile/Cargo` and SDK musl linker compiled a disposable
  package twice: dependency miss followed by hit in a fresh container. This
  confirms inherited S3 settings/credentials reach OpenWrt's sccache wrapper.
- BuildKit checked the exact changed SDK Dockerfile. Only the known public
  `KEY_PREFIX` secret-name heuristic and required base-image argument warning were
  excluded. Workflow/actionlint, ShellCheck, shell/Python/JSON/YAML parsing and
  whitespace checks passed.

The SDK image retains public S3 settings and version-only prefixes. Credentials are not stored in image configuration/history, and runner endpoints
are build arguments rather than runtime defaults. Gitea secrets feed ephemeral BuildKit
mounts and runtime verification containers. The runner service's registration,
labels/capacity, build matrix, image tags, manual publication gate, BuildKit layer
caching and serial verbose fallback are unchanged.

Manual setup: allocate/mount the 100 GiB volume, set the two cache credentials in
Komodo and Gitea, deploy the cache services, initialize the bucket, and run a
non-publishing workflow before cutover. Full SDK bootstrap and live Gitea dispatch
were not repeated for this backend change. Production Redis, its volume, and
Jenkins remain untouched pending acceptance/approved retirement. Isolated test
containers and volumes are removed after verification; logs remain under
`/tmp/s3-cache-tests` on the runner.

Sources: [RustFS deployment](https://docs.rustfs.com/en/installation/container/docker),
[VersityGW Docker setup](https://github.com/versity/versitygw/wiki/Docker),
[sccache S3](https://github.com/mozilla/sccache/blob/v0.18.0/docs/S3.md),
[BuildKit secrets](https://docs.docker.com/build/building/secrets/).
