from autolist.content.sanitizer import sanitize


def test_keeps_allowed_tags_without_attributes() -> None:
    out = sanitize('<p class="x" onclick="a()"><strong>שם</strong></p><ul><li>א</li></ul>')
    assert out == "<p><strong>שם</strong></p><ul><li>א</li></ul>"


def test_drops_script_and_its_content() -> None:
    assert sanitize("<p>a<script>alert(1)</script>b</p>") == "<p>ab</p>"


def test_drops_unknown_tags_keeps_text_escaped() -> None:
    assert sanitize('<a href="javascript:x">קישור</a><img src=x onerror=y>') == "קישור"
    assert sanitize("<p>1 &lt; 2 &amp; 3</p>") == "<p>1 &lt; 2 &amp; 3</p>"


def test_closes_open_tags_and_ignores_stray_closers() -> None:
    assert sanitize("<ul><li>a") == "<ul><li>a</li></ul>"
    assert sanitize("</p>x<br/>") == "x<br>"


def test_nested_drop_content() -> None:
    assert sanitize("<style><svg></svg>x</style>ok") == "ok"
