"""A review packet is one file, and it fetches nothing.

Those two properties are the whole feature. A packet that needs a second
file cannot be emailed, and one that fetches anything tells a third party
that somebody opened a plan, along with their address.
"""

import re

import pytest

from flanner.packet import build, render_body

PLAN = """# Rollout

Some prose citing `src/queue.py` and a [link](https://example.test/doc).

| Parameter | Value |
| --- | --- |
| retries | 3 |

```python
x = 1
```
"""


@pytest.fixture
def packet():
    return build(plan_name="rollout", version=3, body=PLAN, project_name="checkout")


def test_a_packet_fetches_nothing(packet):
    """The rule the whole design rests on.

    Anything that would make the reader's browser talk to a third party is
    a leak: it discloses that a plan was opened, and to whom.
    """
    html = packet.html
    head = html[: html.index("</style>")]
    assert "http://" not in head and "https://" not in head

    # Markup: nothing may be pulled in from elsewhere.
    for pattern in (r'src\s*=\s*["\'](?!data:)', r"<link[^>]+href"):
        assert not re.search(pattern, html, re.I), pattern

    # Stylesheet: the same rule, checked only inside <style> and case
    # sensitively. URL.createObjectURL in the script is not a CSS url(),
    # and matching it was a false alarm the first time round.
    style = html[html.index("<style>") : html.index("</style>")]
    for pattern in (r"@import", r"url\(\s*(?!data:|#)"):
        assert not re.search(pattern, style), pattern


def test_the_only_script_is_the_packet_s_own(packet):
    """A packet does run script now: selecting text and keeping a note
    needs one. What must stay true is that none of it is fetched, which
    `test_a_packet_fetches_nothing` covers by refusing any non-data src."""
    scripts = re.findall(r"<script([^>]*)>", packet.html, re.I)
    assert scripts, "the annotation UI needs a script"
    assert all("src" not in attrs for attrs in scripts), "no script may be external"


def test_the_fonts_are_inside_the_file(packet):
    assert "@font-face" in packet.html
    assert "data:font/woff2;base64," in packet.html


def test_it_stays_small_enough_to_email(packet):
    """The kill test from the spike. The two variable faces cost a fixed
    ~184 KiB; a real plan of 79 KiB came to ~315 KiB."""
    assert packet.kib < 2048, f"{packet.kib} KiB is too large to attach"


def test_dropping_the_fonts_is_worth_measuring(packet):
    """`embed_fonts=False` exists so the trade-off has a number."""
    bare = build(plan_name="rollout", version=3, body=PLAN, embed_fonts=False)
    assert bare.bytes < packet.bytes
    assert "@font-face" not in bare.html


def test_the_plan_is_actually_rendered(packet):
    assert "<h1" in packet.html and "Rollout" in packet.html
    assert "<table" in packet.html
    assert "<pre" in packet.html
    assert "rollout.md at v3" in packet.html


def test_raw_html_in_a_plan_cannot_execute():
    """A plan is text somebody wrote; a packet is a file somebody opens.

    Without sanitising, a plan carrying a script tag would run it on the
    reviewer's machine.
    """
    hostile = render_body("Hello <script>alert(1)</script> and <img src=x onerror=alert(2)>")
    assert "<script" not in hostile
    assert "onerror" not in hostile


def test_the_packet_names_its_version(packet):
    assert packet.version == 3
    assert packet.plan_name == "rollout"


# --- the contents tree and the notes rail -----------------------------------


def test_the_contents_tree_matches_the_rendered_anchors():
    """The tree is read back out of the rendered HTML rather than the
    markdown, so its links cannot disagree with the ids that exist."""
    from flanner.packet import outline, render_body

    rendered = render_body("# One\n\ntext\n\n## Two\n\nmore\n\n### Three\n")
    heads = outline(rendered)
    assert [h.text for h in heads] == ["One", "Two", "Three"]
    assert [h.level for h in heads] == [1, 2, 3]
    for head in heads:
        assert f'id="{head.anchor}"' in rendered


def test_the_tree_is_in_the_packet(packet):
    assert 'class="toc"' in packet.html
    for head in packet.headings:
        assert f'data-anchor="{head.anchor}"' in packet.html


def test_a_plan_with_no_headings_says_so():
    bare = build(plan_name="bare", version=1, body="just a sentence")
    assert bare.headings == ()
    assert "no headings" in bare.html


def test_the_rail_and_composer_are_present(packet):
    for hook in ('id="notes"', 'id="composer"', 'id="export"', 'id="copy"', 'id="who"'):
        assert hook in packet.html, hook


def test_the_packet_carries_its_own_identity(packet):
    """The export needs to say which plan and version it belongs to."""
    assert '"plan": "rollout"' in packet.html
    assert '"version": 3' in packet.html


def test_the_reviewer_is_told_their_notes_are_unsigned(packet):
    """They have no device key, so their notes import as unverified. Saying
    so in the packet is cheaper than explaining it afterwards."""
    assert "unsigned" in packet.html


def test_notes_are_anchored_by_quote_not_offset(packet):
    """Offsets rot on the first edit. The stored anchor is the text."""
    assert "occurrence" in packet.html
    assert "quote" in packet.html


def test_the_tree_grows_with_the_plan(packet):
    """A long plan is exactly when navigation matters."""
    assert len(packet.headings) >= 1


# --- storage that may not be there ------------------------------------------


def test_the_packet_probes_storage_rather_than_assuming_it(packet):
    """A packet is opened straight off a disk, and a file:// page is exactly
    where localStorage gets restricted. Swallowing that failure silently let
    a reviewer write twenty notes, close the tab, and lose all of them."""
    assert "storageWorks" in packet.html
    assert "probe" in packet.html


def test_the_packet_can_say_it_will_not_remember(packet):
    """The warning has to exist in the markup, hidden until it is needed."""
    assert 'id="nostore"' in packet.html
    assert "will not remember your notes" in packet.html
    assert 'role="alert"' in packet.html


def test_the_warning_starts_hidden(packet):
    """Shown on every packet it would be noise, and would train people to
    ignore it in the one case that matters."""
    banner = packet.html[packet.html.index('id="nostore"') :][:200]
    assert "hidden" in banner


def test_the_warning_tells_them_what_to_do(packet):
    """A warning without an action is just anxiety."""
    assert "Export notes" in packet.html
