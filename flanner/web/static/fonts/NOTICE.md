# Bundled fonts

`Geist-Variable.woff2` and `GeistMono-Variable.woff2` are Geist and Geist
Mono, © 2024 The Geist Project Authors, released under the SIL Open Font
License, Version 1.1. The full licence is in `OFL.txt` beside them, as the
OFL requires.

They are vendored rather than fetched from a CDN because the local web UI
has to render correctly with no network at all, which is the same reason
the rest of this application works offline.

The CSS names a fallback stack after each face, so a build that strips
these files degrades to the system UI and monospace fonts rather than
breaking.
