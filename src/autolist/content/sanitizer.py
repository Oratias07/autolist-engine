"""Allowlist HTML sanitizer for product descriptions.

Only the tags the playbook's description sections produce are kept, with no attributes.
Everything else is dropped; text is escaped. Applied to every description before it is
sent to the store, even though the writer's HTML is built in code from escaped strings.
"""

from __future__ import annotations

import html
from html.parser import HTMLParser

ALLOWED = frozenset({"p", "strong", "ul", "ol", "li", "br", "h2", "h3"})
VOID = frozenset({"br"})
DROP_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "template", "svg"})


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self._drop = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_CONTENT:
            self._drop += 1
            return
        if self._drop or tag not in ALLOWED:
            return
        if tag in VOID:
            self.out.append(f"<{tag}>")
            return
        self.out.append(f"<{tag}>")
        self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._drop and tag in VOID:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_CONTENT:
            self._drop = max(0, self._drop - 1)
            return
        if self._drop or tag not in self.open:
            return
        while self.open:
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._drop:
            self.out.append(html.escape(data, quote=False))


def sanitize(fragment: str) -> str:
    parser = _Clean()
    parser.feed(fragment)
    parser.close()
    parser.out.extend(f"</{t}>" for t in reversed(parser.open))
    return "".join(parser.out)
