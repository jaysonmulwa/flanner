# Draft: upstream request for musllinux wheels

Not filed. This is ready to paste into
<https://github.com/n0-computer/iroh/issues/new> when you want it.

**Why it is a draft and not an issue.** Filing speaks as you, in public,
on someone else's project. That is yours to send.

**Why it is worth sending.** It is the only fix. `pip install flanner`
fails outright on Alpine, and no PEP 508 marker can express libc, so
nothing in our packaging can route around it. Their CI already builds
musl targets for `iroh-relay`, so the ask is small.

---

## Title

Publish musllinux wheels (or an sdist) for the Python bindings

## Body

The Python package publishes four wheels and no sdist:

```
iroh-1.1.0-py3-none-macosx_11_0_arm64.whl
iroh-1.1.0-py3-none-manylinux_2_28_aarch64.whl
iroh-1.1.0-py3-none-manylinux_2_28_x86_64.whl
iroh-1.1.0-py3-none-win_amd64.whl
```

On a musl-based distribution such as Alpine, the manylinux wheels do not
match, and with no sdist there is nothing to fall back on. `pip` reports:

```
ERROR: Could not find a version that satisfies the requirement iroh
ERROR: No matching distribution found for iroh
```

That reads as "this package does not exist" rather than "this platform is
not supported", which sends people looking in the wrong place.

**Why a downstream fix is not possible.** A dependency can be made
conditional with an environment marker, and we do that for the platforms
where no wheel exists at all:

```toml
"iroh>=1.1,<2; sys_platform == 'linux' and platform_machine == 'x86_64'",
```

But markers cannot express libc. Alpine reports `sys_platform == 'linux'`
and `platform_machine == 'x86_64'` exactly as glibc does, so the marker
matches and the install fails. There is no expression that excludes musl.

**Two things that would each fix it:**

1. **musllinux wheels.** The repository already builds
   `x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl` targets
   for `iroh-relay` releases, so the toolchain is present. `maturin` can
   emit `musllinux_1_2` wheels from the same cross build.
2. **An sdist.** Slower for users, since it needs a Rust toolchain, but it
   turns a hard resolution failure into a build that can succeed.

The first is much better for users. The second is a smaller change and
would unblock people immediately.

**Also worth noting:** there is no macOS x86-64 wheel either, on 1.1.0 or
on any earlier release. Intel Macs hit the same wall, and there are rather
more of those than Alpine users. That one may be an oversight rather than
a decision.

**Context.** We ship iroh as the peer transport in an MIT-licensed CLI
(<https://github.com/jaysonmulwa/flanner>). It is a required dependency on
the platforms you publish for and omitted elsewhere, so the failure is
confined to musl and Intel macOS.

---

## After filing

Link the issue here, and in the note beside the `iroh` markers in
`pyproject.toml`, so the next person to look at that gap can see whether
it is still open.
