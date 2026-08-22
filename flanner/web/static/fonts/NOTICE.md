# Bundled fonts

`Geist-Variable.woff2` and `GeistMono-Variable.woff2` are Geist and Geist
Mono, © Vercel, released under the SIL Open Font License, Version 1.1.

They are vendored rather than fetched from a CDN because the local web UI
has to render correctly with no network at all, which is the same reason
the rest of this application works offline.

## Incomplete

The OFL requires the full licence text to travel with the font files. That
text is **not yet in this directory**. Copy `OFL.txt` from the upstream
release into this folder before shipping a build that includes these fonts:

    https://github.com/vercel/geist-font

Deliberately not reproduced from memory here — a licence transcribed
approximately is worse than an obvious gap, because it looks settled.
