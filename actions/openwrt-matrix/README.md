# OpenWrt package architecture matrix

This composite Action produces one representative firmware target per OpenWrt
package architecture, for each requested release. It shallow-clones each release
into a separate temporary directory, checks out `refs/tags/v<version>`, and runs
that release's unmodified `scripts/dump-target-info.pl architectures`. The first
target listed on each architecture line is the representative. All grouping and
upstream target eligibility decisions belong to OpenWrt.

## Inputs and output

| Input | Default | Meaning |
| --- | --- | --- |
| `versions` | `stable,oldstable` | Comma-separated aliases and/or explicit versions, e.g. `stable,24.10.8,23.05.6`. Explicit versions omit the `v` tag prefix; release candidates such as `25.12.0-rc4` are accepted. |
| `package-architectures` | `all` | Comma-separated package architecture names from the upstream output, e.g. `aarch64_cortex-a53,x86_64`. |
| `targets` | `all` | Comma-separated firmware targets using `board/subtarget`, e.g. `mediatek/filogic,x86/64`. |

The Action retrieves [OpenWrt's `.versions.json`](https://downloads.openwrt.org/.versions.json)
and resolves aliases using `stable_version` and `oldstable_version`. Explicit
versions are validated by fetching their upstream release tag, so older tags
need not appear in the current metadata's version list. Resolved versions are
deduplicated in request order. Within each release, rows are sorted by package
architecture; upstream target order is preserved when choosing representatives.

Target filtering selects architecture groups containing **any** requested target.
It still emits the first upstream-listed representative, which may differ from
the requested target. For example, if `mediatek/filogic` belongs to
`aarch64_cortex-a53` and `bcm27xx/bcm2710` is listed first, the row uses
`bcm27xx/bcm2710`. Multiple requested targets sharing an architecture produce one
row. When both filters are supplied they intersect. Every explicitly requested
architecture and target must exist in every requested release, even if the other
filter would exclude it. `all` cannot be combined with named selections.

The `matrix` output is compact JSON suitable for `fromJSON`, shaped like:

```json
{"include":[{"version":"24.10.8","package_arch":"x86_64","target":"x86/64"}]}
```

Invalid or unresolved inputs, missing release tags, metadata retrieval failures,
invalid upstream output, and empty selections fail the Action. A release with an
empty filter intersection also fails; combinations are never silently skipped.
No registry, SDK image, Rust support, or custom ABI checks are performed.
Downstream jobs own image selection and compilation failures.

## GitHub and Gitea usage

The Action has no dependency on a forge-specific checkout action. It requires a
Linux runner with Bash, Python 3.8+, Git, GNU Make, Perl, standard Unix tools, CA
certificates, and outbound HTTPS access to downloads.openwrt.org and GitHub.
Python uses only the standard library. Gitea must support composite Actions,
`github.action_path`, and the standard `GITHUB_OUTPUT` environment file.

With this repository checked out, use the same local Action on either forge:

```yaml
jobs:
  matrix:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.openwrt.outputs.matrix }}
    steps:
      - uses: actions/checkout@v4 # On this repository's Gitea runner: builtin:checkout
      - uses: ./actions/openwrt-matrix
        id: openwrt
        with:
          versions: stable,oldstable
          package-architectures: all
          targets: all

  build:
    needs: matrix
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.matrix.outputs.matrix) }}
    steps:
      - name: Consume the matrix
        env:
          VERSION: ${{ matrix.version }}
          PACKAGE_ARCH: ${{ matrix.package_arch }}
          TARGET: ${{ matrix.target }}
        run: |
          printf 'OpenWrt %s: %s via %s\n' "$VERSION" "$PACKAGE_ARCH" "$TARGET"
          # Pull the desired SDK image and compile your package here.
          # If an image tag needs dashes: SDK_TARGET=${TARGET//\//-}
```

For another repository on Gitea, the remote reference is:

```yaml
- uses: https://git.lochnair.net/Lochnair-Labs/openwrt-sdk-rust/actions/openwrt-matrix@main
  id: openwrt
```

Pin a reviewed commit instead of `main` for reproducible Action code. On GitHub,
use a checkout of this repository (or a GitHub mirror) and the local path above;
GitHub's remote `uses: owner/repo/path@ref` syntax only fetches GitHub repositories.
The consumer's repository must be checked out separately if its build needs it.

## Validation and limitations

Run the offline suite independently of the existing SDK matrix tests:

```sh
python3 -m unittest discover -s actions/openwrt-matrix -p 'test_*.py'
python3 -m unittest discover -s ci -p 'test_matrix.py'
```

For a live smoke test (clones upstream releases into temporary directories):

```sh
GITHUB_OUTPUT=/tmp/openwrt-matrix-output MATRIX_TARGETS=x86/64 \
  python3 actions/openwrt-matrix/matrix.py
```

Older release tags must contain a working upstream metadata script; failures are
reported without a fallback classification. The upstream Perl script does not
propagate all child Make exit codes. The wrapper rejects unexpected stderr to
catch visible failures, allowing only the known `include/target.mk` CPU_TYPE
warnings from the incomplete CPU-flags table in DUMP mode. Errors that upstream
itself suppresses remain an upstream limitation. No feeds, toolchain builds, or
container infrastructure are needed.

The existing SDK-building workflow and `ci/matrix.py` remain independent and
retain their existing image/Rust filtering behavior.
