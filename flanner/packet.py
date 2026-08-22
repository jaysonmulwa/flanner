"""A plan as one file somebody outside your mesh can open and annotate.

A review packet is a single self-contained HTML document. No server, no
upload, no account, no install: the recipient double-clicks it. That is the
whole point, and it is the only sharing shape available to us — this
product's argument is that plan content never reaches our servers, so a
hosted share page is not a cheaper option, it is a different product.

Three rules the rest of this module exists to keep:

**Nothing is fetched.** Every byte the packet needs is inside it, including
the fonts. A packet that phones home would leak that a plan was opened, to
whoever serves the font, along with the reader's address.

**The document is clean by default.** A packet built from a plan carrying
internal review does not carry that review to a client. Including your
team's comments has to be asked for, because the cost of getting that
default wrong is disclosing something to the wrong party.

**Notes are anchored to a quote, not to a position.** Offsets rot the
moment the plan is edited. A note that silently moves to the wrong
paragraph is worse than one that says it is stranded.

The CSS and the script are plain strings rather than f-strings. Braces are
the majority character in both, and doubling every one of them to satisfy
a formatter is how a stylesheet becomes unreadable.
"""

from __future__ import annotations

import base64
import html as html_module
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import markdown
import nh3

STATIC = Path(__file__).parent / "web" / "static"
FONTS = STATIC / "fonts"

# The same set the local web UI sanitises with, so a plan renders in a packet
# exactly as it renders at home.
SANITIZE_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "div", "span",
    "strong", "em", "b", "i", "u", "s", "code", "pre", "kbd",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
}  # fmt: skip
SANITIZE_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "*": {"class", "id"},
}

_HEADING = re.compile(r'<h([123])[^>]*\sid="([^"]+)"[^>]*>(.*?)</h\1>', re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Heading:
    """One entry in the contents tree."""

    level: int
    anchor: str
    text: str


@dataclass(frozen=True)
class Packet:
    """A rendered packet, and what it cost to make one."""

    html: str
    plan_name: str
    version: int
    headings: tuple[Heading, ...] = ()

    @property
    def bytes(self) -> int:
        return len(self.html.encode("utf-8"))

    @property
    def kib(self) -> float:
        return round(self.bytes / 1024, 1)


def render_body(text: str) -> str:
    """Markdown to sanitised HTML, matching the local web UI's rendering."""
    md = markdown.Markdown(
        extensions=["fenced_code", "codehilite", "tables", "toc", "nl2br"],
        extension_configs={"codehilite": {"css_class": "highlight", "linenums": False}},
    )
    return nh3.clean(md.convert(text), tags=SANITIZE_TAGS, attributes=SANITIZE_ATTRS)


def outline(rendered: str) -> tuple[Heading, ...]:
    """The contents tree, read back out of the rendered HTML.

    Taken from the output rather than the markdown source so the anchors are
    exactly the ones the toc extension generated, which is what the links
    have to match.
    """
    found = []
    for level, anchor, inner in _HEADING.findall(rendered):
        text = html_module.unescape(_TAGS.sub("", inner)).strip()
        if text:
            found.append(Heading(level=int(level), anchor=anchor, text=text))
    return tuple(found)


def _font_face(family: str, filename: str) -> str:
    """One @font-face rule with the file inlined as a data URI.

    Base64 costs a third more than the raw bytes. That is the price of a
    packet that renders identically on a machine that has never heard of
    this product and may have no network at all.
    """
    raw = (FONTS / filename).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return (
        f"@font-face{{font-family:'{family}';"
        f"src:url(data:font/woff2;base64,{encoded}) format('woff2-variations');"
        "font-weight:100 900;font-display:swap}"
    )


def _fonts() -> str:
    return _font_face("Geist", "Geist-Variable.woff2") + _font_face(
        "Geist Mono", "GeistMono-Variable.woff2"
    )


_CSS = """
:root {
  --bg:#f7f7f8; --card:#fff; --rail:#fbfbfc; --border:#e7e7ea; --divider:#ececef;
  --text:#17181c; --text-2:#4b4c53; --text-3:#6b6c72; --text-4:#6f7078;
  --accent:#157e58; --accent-soft:#e9f7f0; --accent-line:#cdeadd;
  --chip:#eef0f0; --mark:#fff3c4; --mark-line:#e9b949;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#141519; --card:#1c1d22; --rail:#17181c; --border:#2a2b31;
    --divider:#26272c; --text:#f2f3f5; --text-2:#b6b7bd; --text-3:#9a9ba3;
    --text-4:#85868c; --accent:#39a27c; --accent-soft:rgba(23,128,90,.16);
    --accent-line:rgba(45,212,143,.28); --chip:#26272c;
    --mark:rgba(233,185,73,.22); --mark-line:#e9b949;
  }
}
*,*::before,*::after { box-sizing:border-box; }
body {
  margin:0; background:var(--bg); color:var(--text);
  font:400 15px/1.65 var(--sans); -webkit-font-smoothing:antialiased;
}
.shell { display:flex; align-items:flex-start; min-height:100vh; }

/* --- contents --------------------------------------------------------- */
.toc {
  width:250px; flex:none; position:sticky; top:0; height:100vh; overflow-y:auto;
  background:var(--rail); border-right:1px solid var(--border); padding:20px 14px;
}
.toc h2, .notes h2 {
  font:600 10.5px var(--mono); color:var(--text-4); letter-spacing:.06em;
  text-transform:uppercase; margin:0 0 12px; padding:0 6px;
}
.toc a {
  display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:7px;
  color:var(--text-2); text-decoration:none; font-size:13px; line-height:1.35;
}
.toc a:hover { background:var(--chip); color:var(--text); }
.toc a.on { background:var(--chip); color:var(--text); font-weight:600; }
.toc a .n {
  margin-left:auto; font:600 10.5px var(--mono); color:var(--accent);
  background:var(--accent-soft); border-radius:9px; padding:1px 6px;
}
.toc .l2 { padding-left:20px; }
.toc .l3 { padding-left:34px; font-size:12.5px; }

/* --- document --------------------------------------------------------- */
.main { flex:1; min-width:0; padding:28px 32px 90px; }
header.packet { display:flex; align-items:center; gap:10px; margin-bottom:20px; }
.mark {
  width:26px; height:26px; border-radius:7px; flex:none;
  background:linear-gradient(135deg,#17805a,#35c98e);
  display:inline-flex; align-items:center; justify-content:center;
  color:#fff; font:700 13px var(--mono);
}
.who { font-size:13px; color:var(--text-3); }
.doc {
  background:var(--card); border:1px solid var(--border);
  border-radius:14px; padding:30px 34px; max-width:64ch;
}
.doc h1 { font-size:24px; letter-spacing:-.02em; margin:0 0 18px; }
.doc h2 { font-size:18px; margin:28px 0 10px; }
.doc h3 { font-size:15px; margin:22px 0 8px; }
.doc p, .doc li { color:var(--text-2); }
.doc code {
  font:500 .88em var(--mono); background:var(--chip); padding:1px 5px; border-radius:4px;
}
.doc pre {
  background:#17181c; color:#d1d9e0; padding:16px 18px; border-radius:10px;
  overflow-x:auto;
}
.doc pre code { background:none; padding:0; color:inherit; }
.doc table { border-collapse:collapse; width:100%; margin:14px 0; }
.doc th, .doc td {
  text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); font-size:14px;
}
.doc th { color:var(--text-3); font-size:12px; text-transform:uppercase; }
.doc a { color:var(--accent); }
.doc blockquote {
  margin:14px 0; padding:2px 0 2px 14px; border-left:3px solid var(--border);
  color:var(--text-3);
}
.doc mark.note {
  background:var(--mark); border-bottom:2px solid var(--mark-line);
  color:inherit; padding:1px 0; cursor:pointer;
}
.doc mark.note.on { background:var(--accent-soft); border-bottom-color:var(--accent); }
footer.packet { margin-top:20px; font-size:12px; color:var(--text-4); max-width:64ch; }

/* --- notes ------------------------------------------------------------- */
.notes {
  width:300px; flex:none; position:sticky; top:0; height:100vh; overflow-y:auto;
  background:var(--rail); border-left:1px solid var(--border);
  padding:20px 14px; display:flex; flex-direction:column;
}
.note-card {
  background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; cursor:pointer;
}
.note-card.on { border-color:var(--accent); }
.note-card .quote {
  font:500 11.5px var(--mono); color:var(--text-3); margin-bottom:6px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow:hidden; border-left:2px solid var(--mark-line); padding-left:7px;
}
.note-card .body { font-size:13px; color:var(--text); white-space:pre-wrap; }
.note-card .meta { margin-top:6px; font-size:11px; color:var(--text-4); }
.note-card .kill {
  float:right; border:0; background:none; color:var(--text-4); cursor:pointer;
  font-size:14px; line-height:1; padding:0 2px;
}
.note-card .kill:hover { color:#e5484d; }
.empty { font-size:12.5px; color:var(--text-4); line-height:1.6; padding:0 6px; }
.nostore {
  border:1px solid var(--mark-line); background:var(--mark); border-radius:9px;
  padding:9px 11px; margin-bottom:10px; font-size:12px; line-height:1.5;
  color:var(--text);
}
.foot { margin-top:auto; padding-top:14px; border-top:1px solid var(--divider); }
.btn {
  display:inline-flex; align-items:center; justify-content:center; gap:6px;
  font:500 13px var(--sans); color:var(--text-2); background:var(--card);
  border:1px solid var(--border); border-radius:8px; padding:8px 12px;
  cursor:pointer; width:100%; margin-bottom:6px;
}
.btn:hover { color:var(--text); border-color:var(--text-4); }
.btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.btn.primary:hover { color:#fff; opacity:.92; }
.who-field {
  width:100%; font:400 12.5px var(--sans); color:var(--text);
  background:var(--card); border:1px solid var(--border); border-radius:8px;
  padding:7px 9px; margin-bottom:8px;
}
.hint { font-size:11.5px; color:var(--text-4); line-height:1.5; margin:8px 0 0; }

/* --- composer ----------------------------------------------------------- */
.composer {
  position:fixed; z-index:50; width:300px; background:var(--card);
  border:1px solid var(--border); border-radius:12px; padding:12px;
  box-shadow:0 18px 40px rgba(10,12,16,.18);
}
.composer textarea {
  width:100%; min-height:74px; resize:vertical; font:400 13px var(--sans);
  color:var(--text); background:var(--bg); border:1px solid var(--border);
  border-radius:8px; padding:8px 9px;
}
.composer .row { display:flex; gap:6px; margin-top:8px; }
.composer .row .btn { margin:0; }
[hidden] { display:none !important; }

@media (max-width:1100px) {
  .toc { display:none; }
}
@media (max-width:820px) {
  .shell { flex-direction:column; }
  .notes {
    width:auto; height:auto; position:static; border-left:0;
    border-top:1px solid var(--border);
  }
  .main { padding:20px 16px 40px; width:100%; }
  .composer { width:calc(100vw - 32px); }
}
@media print {
  .toc, .notes, .composer { display:none; }
  .doc { border:0; padding:0; }
}
"""

_JS = r"""
(function () {
  var DATA = window.__PACKET__;
  var KEY = 'flanner.packet.' + DATA.plan + '.v' + DATA.version;
  var doc = document.getElementById('doc');
  var rail = document.getElementById('notes');
  var composer = document.getElementById('composer');
  var field = document.getElementById('note-text');
  var whoField = document.getElementById('who');
  var pending = null;

  // Whether this browser will actually keep anything. A packet is opened
  // straight off a disk, and a file:// page is exactly where storage gets
  // restricted; a private window is the other case. Swallowing the failure
  // silently meant a reviewer could write twenty notes, close the tab and
  // lose all of them without ever being told.
  var storageWorks = (function () {
    try {
      var probe = KEY + '.probe';
      localStorage.setItem(probe, '1');
      localStorage.removeItem(probe);
      return true;
    } catch (e) {
      return false;
    }
  })();

  function tellThemStorageIsOff() {
    var banner = document.getElementById('nostore');
    if (banner) banner.hidden = false;
  }

  function load() {
    if (!storageWorks) return {};
    try { return JSON.parse(localStorage.getItem(KEY) || '{}'); }
    catch (e) { return {}; }
  }

  function save(state) {
    if (!storageWorks) return false;
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
      return true;
    } catch (e) {
      // Worked a moment ago and does not now: out of quota, most likely.
      // Same consequence for the reviewer, so the same warning.
      storageWorks = false;
      tellThemStorageIsOff();
      return false;
    }
  }

  if (!storageWorks) tellThemStorageIsOff();

  var state = load();
  if (!state.notes) state.notes = [];
  if (whoField) {
    whoField.value = state.reviewer || '';
    whoField.addEventListener('input', function () {
      state.reviewer = whoField.value.trim();
      save(state);
    });
  }

  // --- anchoring ---------------------------------------------------------
  // A quote plus which occurrence of it, found by one function used at both
  // ends. The first attempt measured the selection one way and searched for
  // it another, so the two disagreed and every note came out stranded.
  // Whatever is wrong now is wrong identically in both directions.
  function occurrences(quote) {
    var found = [];
    if (!quote) return found;
    var walker = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      var at = node.nodeValue.indexOf(quote);
      while (at !== -1) {
        var range = document.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + quote.length);
        found.push(range);
        at = node.nodeValue.indexOf(quote, at + 1);
      }
    }
    return found;
  }

  function whichOccurrence(quote, selection) {
    var all = occurrences(quote);
    if (!selection.rangeCount) return 0;
    var live = selection.getRangeAt(0);
    for (var i = 0; i < all.length; i++) {
      // A selection spanning two elements will not match any single text
      // node, so this can miss. Falling back to the first occurrence puts
      // the mark somewhere defensible rather than nowhere.
      if (all[i].compareBoundaryPoints(Range.START_TO_START, live) === 0) return i;
    }
    return 0;
  }

  function sectionOf(node) {
    var el = node.nodeType === 3 ? node.parentElement : node;
    while (el && el !== doc) {
      var prev = el.previousElementSibling;
      while (prev) {
        if (/^H[1-6]$/.test(prev.tagName) && prev.id) return prev.id;
        prev = prev.previousElementSibling;
      }
      el = el.parentElement;
    }
    return "";
  }

  // --- writing a note -----------------------------------------------------
  doc.addEventListener('mouseup', function () {
    var sel = window.getSelection();
    var quote = sel && sel.toString().trim();
    if (!quote || quote.length < 2) return;
    if (!doc.contains(sel.anchorNode)) return;

    var rect = sel.getRangeAt(0).getBoundingClientRect();
    pending = {
      quote: quote,
      section: sectionOf(sel.anchorNode),
      occurrence: whichOccurrence(quote, sel)
    };

    composer.hidden = false;
    var top = Math.min(rect.bottom + window.scrollY + 8,
                       window.scrollY + window.innerHeight - 190);
    composer.style.top = top + 'px';
    composer.style.left = Math.max(12, Math.min(rect.left,
                          window.innerWidth - 320)) + 'px';
    field.value = '';
    field.focus();
  });

  function closeComposer() {
    composer.hidden = true;
    pending = null;
  }

  document.getElementById('note-cancel').addEventListener('click', closeComposer);
  document.getElementById('note-save').addEventListener('click', commit);
  field.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeComposer();
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commit();
  });

  function commit() {
    var body = field.value.trim();
    if (!pending || !body) return closeComposer();
    state.notes.push({
      id: 'n' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      quote: pending.quote,
      section: pending.section,
      occurrence: pending.occurrence,
      body: body,
      at: new Date().toISOString()
    });
    save(state);
    closeComposer();
    paint();
  }

  // --- painting -----------------------------------------------------------
  function clearMarks() {
    doc.querySelectorAll('mark.note').forEach(function (m) {
      var parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });
  }

  function highlight(note) {
    var all = occurrences(note.quote);
    var range = all[note.occurrence] || all[0];
    if (!range) return false;
    var mark = document.createElement("mark");
    mark.className = "note";
    mark.dataset.note = note.id;
    try {
      range.surroundContents(mark);
      return true;
    } catch (e) {
      // The quote straddles two elements, so it cannot be wrapped in one
      // node. The note is not stranded; it just cannot be shown inline.
      return false;
    }
  }

  function paint() {
    clearMarks();
    var counts = {};
    state.notes.forEach(function (note) {
      note.stranded = !highlight(note);
      counts[note.section] = (counts[note.section] || 0) + 1;
    });

    rail.innerHTML = '';
    if (!state.notes.length) {
      var blank = document.createElement('p');
      blank.className = 'empty';
      blank.textContent = 'No notes yet. Select any text in the plan to leave one.';
      rail.appendChild(blank);
    }
    state.notes.forEach(function (note) {
      var card = document.createElement('div');
      card.className = 'note-card';
      card.dataset.note = note.id;

      var kill = document.createElement('button');
      kill.className = 'kill';
      kill.textContent = '×';
      kill.title = 'Delete this note';
      kill.addEventListener('click', function (e) {
        e.stopPropagation();
        state.notes = state.notes.filter(function (n) { return n.id !== note.id; });
        save(state);
        paint();
      });
      card.appendChild(kill);

      var quote = document.createElement('div');
      quote.className = 'quote';
      quote.textContent = note.quote;
      card.appendChild(quote);

      var body = document.createElement('div');
      body.className = 'body';
      body.textContent = note.body;
      card.appendChild(body);

      var meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = note.stranded
        ? 'the quoted text is no longer in this plan'
        : new Date(note.at).toLocaleString();
      card.appendChild(meta);

      card.addEventListener('click', function () { focusNote(note.id); });
      rail.appendChild(card);
    });

    document.querySelectorAll('.toc a').forEach(function (link) {
      var badge = link.querySelector('.n');
      var n = counts[link.dataset.anchor] || 0;
      if (badge) badge.remove();
      if (n) {
        var el = document.createElement('span');
        el.className = 'n';
        el.textContent = n;
        link.appendChild(el);
      }
    });

    document.getElementById('count').textContent = state.notes.length
      ? state.notes.length + (state.notes.length === 1 ? ' note' : ' notes')
      : '';
  }

  function focusNote(id) {
    document.querySelectorAll('.note-card, mark.note').forEach(function (el) {
      el.classList.toggle('on', el.dataset.note === id);
    });
    var mark = doc.querySelector('mark.note[data-note="' + id + '"]');
    if (mark) mark.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  doc.addEventListener('click', function (e) {
    var mark = e.target.closest('mark.note');
    if (mark) focusNote(mark.dataset.note);
  });

  // --- export --------------------------------------------------------------
  function review() {
    return {
      packet: { plan: DATA.plan, version: DATA.version, project: DATA.project },
      reviewer: (state.reviewer || '').trim(),
      exported_at: new Date().toISOString(),
      notes: state.notes.map(function (n) {
        return {
          id: n.id, quote: n.quote, section: n.section,
          occurrence: n.occurrence, body: n.body, at: n.at
        };
      })
    };
  }

  document.getElementById('export').addEventListener('click', function () {
    if (!state.notes.length) return;
    var text = JSON.stringify(review(), null, 2);
    var name = DATA.plan + '.v' + DATA.version + '.review.json';
    var blob = new Blob([text], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });

  // A download can be blocked when a file is opened straight off a disk,
  // so there is always a way to get the notes out by hand.
  document.getElementById('copy').addEventListener('click', function () {
    var text = JSON.stringify(review(), null, 2);
    var button = document.getElementById('copy');
    var said = button.textContent;
    function done(msg) {
      button.textContent = msg;
      setTimeout(function () { button.textContent = said; }, 1600);
    }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(function () { done('Copied'); },
                                               function () { done('Press Ctrl+C'); });
    } else { done('Press Ctrl+C'); }
  });

  // --- contents ------------------------------------------------------------
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a'));
  if (window.IntersectionObserver && links.length) {
    var seen = new Map();
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { seen.set(entry.target.id, entry.isIntersecting); });
      var active = links.filter(function (l) { return seen.get(l.dataset.anchor); })[0];
      links.forEach(function (l) { l.classList.toggle('on', l === active); });
    }, { rootMargin: '-10% 0px -75% 0px' });
    links.forEach(function (l) {
      var head = document.getElementById(l.dataset.anchor);
      if (head) spy.observe(head);
    });
  }

  paint();
})();
"""

_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__FONTS__
:root { --sans:__SANS__; --mono:__MONO__; }
__CSS__
</style>
</head>
<body>
<div class="shell">

  <nav class="toc" aria-label="Contents">
    <h2>Contents</h2>
__TOC__
  </nav>

  <div class="main">
    <header class="packet">
      <span class="mark" aria-hidden="true">f</span>
      <span class="who">__WHERE__review packet</span>
    </header>
    <article class="doc" id="doc">
__BODY__
    </article>
    <footer class="packet">
      __PLAN__.md at v__VERSION__. Everything here stays in this file: your
      notes are kept by your own browser, and nothing is sent anywhere.
    </footer>
  </div>

  <aside class="notes" aria-label="Notes">
    <h2>Your notes <span id="count"></span></h2>
    <div class="nostore" id="nostore" role="alert" hidden>
      <b>This browser will not remember your notes.</b>
      They are safe while this tab is open, and they will be gone if you close
      or reload it. Press <b>Export notes</b> before you leave.
    </div>
    <div id="notes"></div>
    <div class="foot">
      <input class="who-field" id="who" type="text" placeholder="Your name"
             autocomplete="name" spellcheck="false">
      <button class="btn primary" id="export">Export notes</button>
      <button class="btn" id="copy">Copy as JSON</button>
      <p class="hint">Send the exported file back to whoever gave you this
         plan. Your notes are unsigned, so they will be recorded as coming
         from outside the team.</p>
    </div>
  </aside>
</div>

<div class="composer" id="composer" hidden>
  <textarea id="note-text" placeholder="What about this?"
            aria-label="Your note"></textarea>
  <div class="row">
    <button class="btn primary" id="note-save">Add note</button>
    <button class="btn" id="note-cancel">Cancel</button>
  </div>
</div>

<script>window.__PACKET__ = __DATA__;</script>
<script>
__JS__
</script>
</body>
</html>
"""


def _toc_html(headings: tuple[Heading, ...]) -> str:
    if not headings:
        return '    <p class="empty">This plan has no headings.</p>'
    rows = []
    for head in headings:
        text = html_module.escape(head.text)
        anchor = html_module.escape(head.anchor, quote=True)
        rows.append(
            f'    <a class="l{head.level}" href="#{anchor}" data-anchor="{anchor}">{text}</a>'
        )
    return "\n".join(rows)


def build(
    *,
    plan_name: str,
    version: int,
    body: str,
    project_name: str = "",
    authored_at: datetime | None = None,
    embed_fonts: bool = True,
) -> Packet:
    """One plan, at one version, as a standalone document somebody can mark up.

    ``embed_fonts`` exists to be measured rather than assumed: the two
    variable faces are the largest thing in the file by far, and whether
    they are worth their weight is a decision that needs a number.
    """
    rendered = render_body(body)
    headings = outline(rendered)
    stamp = authored_at.strftime("%d %B %Y") if authored_at else ""
    where = f"{project_name} · " if project_name else ""
    if stamp:
        where = f"{where}{stamp} · "

    data = json.dumps(
        {"plan": plan_name, "version": version, "project": project_name},
        ensure_ascii=True,
    )

    html = (
        _SHELL.replace("__TITLE__", html_module.escape(f"{plan_name}.md — review packet"))
        .replace("__FONTS__", _fonts() if embed_fonts else "")
        .replace("__SANS__", "'Geist',sans-serif" if embed_fonts else "ui-sans-serif,sans-serif")
        .replace("__MONO__", "'Geist Mono',monospace" if embed_fonts else "ui-monospace,monospace")
        .replace("__CSS__", _CSS)
        .replace("__TOC__", _toc_html(headings))
        .replace("__WHERE__", html_module.escape(where))
        .replace("__BODY__", rendered)
        .replace("__PLAN__", html_module.escape(plan_name))
        .replace("__VERSION__", str(version))
        .replace("__DATA__", data)
        .replace("__JS__", _JS)
    )
    return Packet(html=html, plan_name=plan_name, version=version, headings=headings)
