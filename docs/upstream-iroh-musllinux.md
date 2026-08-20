# Draft: upstream request for musllinux wheels

Not filed. Ready to paste into
<https://github.com/n0-computer/iroh-ffi/issues/new> when you want it.

**Why it is a draft.** Filing speaks as you, in public, on someone else's
project. That is yours to send.

**Why `iroh-ffi` and not `iroh`.** The Python wheels are built and
published from `iroh-ffi/.github/workflows/wheels.yml`. An issue on the
main `iroh` repository would reach the wrong maintainers.

**Why it is worth sending.** It is the only fix. `pip install flanner`
fails outright on Alpine, and no PEP 508 marker can express libc, so
nothing in our packaging can route around it.

---

## Title

Add a musllinux job to `wheels.yml`

## Body

`iroh` on PyPI publishes four wheels and no sdist:

```
iroh-1.1.0-py3-none-macosx_11_0_arm64.whl
iroh-1.1.0-py3-none-manylinux_2_28_aarch64.whl
iroh-1.1.0-py3-none-manylinux_2_28_x86_64.whl
iroh-1.1.0-py3-none-win_amd64.whl
```

On a musl-based distribution such as Alpine the manylinux wheels do not
match, and with no sdist there is nothing to fall back on:

```
ERROR: Could not find a version that satisfies the requirement iroh
ERROR: No matching distribution found for iroh
```

That reads as "this package does not exist" rather than "this platform is
not supported", which sends people looking in the wrong place.

### Why this cannot be fixed downstream

A dependency can be made conditional with an environment marker, and we do
that for the platforms with no wheel at all:

```toml
"iroh>=1.1,<2; sys_platform == 'linux' and platform_machine == 'x86_64'",
```

But markers cannot express libc. Alpine reports `sys_platform == 'linux'`
and `platform_machine == 'x86_64'` exactly as glibc does, so the marker
matches and the install fails anyway. There is no expression that excludes
musl.

### The change looks small from the outside

`wheels.yml` already has a `manylinux` job that runs `maturin` inside the
PyPA container. A musllinux job would be the same shape:

```yaml
  musllinux:
    runs-on: ${{ matrix.runner }}
    container: ${{ matrix.container }}
    strategy:
      matrix:
        include:
          - release-os: musllinux_1_2
            release-arch: x86_64
            container: quay.io/pypa/musllinux_1_2_x86_64
            runner: [self-hosted, linux, X64]
          - release-os: musllinux_1_2
            release-arch: aarch64
            container: quay.io/pypa/musllinux_1_2_aarch64
            runner: [self-hosted, linux, ARM64]
```

with a `Makefile.toml` task beside the existing one:

```toml
[tasks.python-wheel-musllinux]
description = "Build a musllinux_1_2 release wheel (run inside the musllinux container)."
command = "maturin"
args = ["build", "--release", "--compatibility", "musllinux_1_2"]
```

Two things suggest this is less work than it might be:

- **The musl Rust targets are already built in this repository.**
  `release.yml` builds `x86_64-unknown-linux-musl` and
  `aarch64-unknown-linux-musl`, so the cross-compilation is proven here
  and not a new problem.
- The PyPA musllinux containers are drop-in equivalents of the manylinux
  ones the job already uses.

I have not opened a PR because the jobs run on self-hosted runners I
cannot test against. Happy to if it would help.

### An sdist would also unblock it

Slower for users, since it needs a Rust toolchain, but it turns a hard
resolution failure into a build that can succeed. Less good than wheels,
and much less work if the runner situation makes musllinux awkward.

### Related: x86-64 macOS

I see the comment in `wheels.yml`:

> arm64-only: the org has no GitHub-hosted macOS and x86_64 mac wheels
> were dropped.

Understood, and not what this issue is asking for. Worth noting only that
GitHub's hosted `macos-13` runners are Intel and free for public
repositories, if the constraint was runner availability rather than a
decision to stop supporting Intel.

### Context

We ship `iroh` as the peer transport in an MIT-licensed CLI
(<https://github.com/jaysonmulwa/flanner>). It is a required dependency on
the platforms you publish for and omitted elsewhere, so the failure is
confined to musl and Intel macOS. Thanks for the library — the
dial-by-key model fit our design almost exactly.

---

## After filing

Link the issue here and beside the `iroh` markers in `pyproject.toml`, so
whoever next looks at that gap can see whether it is still open.
