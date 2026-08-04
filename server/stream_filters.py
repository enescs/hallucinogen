"""Models don't hand you clean HTML.

Qwen3 emits <think> blocks, most models wrap output in ```html fences, and some
chat about what they're about to do. This strips all of that out of a token
stream without ever holding the stream up.
"""

from __future__ import annotations

import html as html_mod
import re

OPEN_TAGS = ("<think>", "<thinking>")
CLOSE_TAGS = ("</think>", "</thinking>")
_MAX_TAG = max(len(t) for t in OPEN_TAGS + CLOSE_TAGS)

_DOC_START_RE = re.compile(
    r"<(!doctype|html|head|body|main|div|section|article|header|h1|style|meta|table|center|font)\b", re.I
)
_FENCE_RE = re.compile(r"^\s*```[a-z]*[ \t]*\r?\n?", re.I)
_TRAILING_FENCE_RE = re.compile(r"\s*```[a-z]*\s*$", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

_HOLD = 8  # tail held back so a trailing ``` never reaches the page


def _first_of(haystack: str, needles) -> tuple[int, str]:
    best = (-1, "")
    for tag in needles:
        i = haystack.find(tag)
        if i != -1 and (best[0] == -1 or i < best[0]):
            best = (i, tag)
    return best


def _dangling_prefix(text: str, tags) -> int:
    """Longest suffix of `text` that could still grow into one of `tags`."""
    for k in range(min(len(text), _MAX_TAG - 1), 0, -1):
        tail = text[-k:]
        if any(tag.startswith(tail) for tag in tags):
            return k
    return 0


class HtmlCleaner:
    """push() returns what is safe to emit now; flush() returns the remainder."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self._started = False
        self._head = ""
        self._hold = ""

    def _strip_think(self, chunk: str, final: bool) -> str:
        self._buf += chunk
        out = []
        while True:
            if self._in_think:
                i, tag = _first_of(self._buf, CLOSE_TAGS)
                if i == -1:
                    keep = 0 if final else _dangling_prefix(self._buf, CLOSE_TAGS)
                    self._buf = self._buf[len(self._buf) - keep :] if keep else ""
                    break
                self._buf = self._buf[i + len(tag) :]
                self._in_think = False
                continue

            i, tag = _first_of(self._buf, OPEN_TAGS)
            if i == -1:
                keep = 0 if final else _dangling_prefix(self._buf, OPEN_TAGS)
                cut = len(self._buf) - keep
                out.append(self._buf[:cut])
                self._buf = self._buf[cut:]
                break

            out.append(self._buf[:i])
            self._buf = self._buf[i + len(tag) :]
            self._in_think = True
        return "".join(out)

    def _strip_preamble(self, chunk: str, final: bool) -> str:
        """Drop a leading ```html fence and any "Sure, here's the page:" chatter."""
        if self._started:
            return chunk
        self._head += chunk

        fence = _FENCE_RE.match(self._head)
        if fence:
            self._head = self._head[fence.end() :]
        elif not final and re.fullmatch(r"\s*`{1,2}", self._head):
            return ""

        match = _DOC_START_RE.search(self._head)
        if not match:
            if not final and len(self._head) < 400:
                return ""  # still might be preamble
            self._started = True
            out, self._head = self._head, ""
            return out

        self._started = True
        out, self._head = self._head[match.start() :], ""
        return out

    def push(self, chunk: str) -> str:
        text = self._hold + self._strip_preamble(self._strip_think(chunk, False), False)
        if len(text) <= _HOLD:
            self._hold = text
            return ""
        self._hold = text[-_HOLD:]
        return text[:-_HOLD]

    def flush(self) -> str:
        tail = self._hold + self._strip_preamble(self._strip_think("", True), True)
        self._hold = ""
        return _TRAILING_FENCE_RE.sub("", tail)


def extract_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    return html_mod.unescape(re.sub(r"\s+", " ", match.group(1))).strip()[:160]


def looks_truncated(html: str) -> bool:
    return bool(html) and "</html>" not in html[-2000:].lower()


def close_document(html: str) -> str:
    """A page cut off mid-tag still has to render. Close what's obviously open."""
    if not looks_truncated(html):
        return html
    tail = html.lower()
    patch = ""
    # An unterminated <script> or <style> would swallow the rest of the page.
    if tail.rfind("<script") > tail.rfind("</script>"):
        patch += "\n</script>"
    if tail.rfind("<style") > tail.rfind("</style>"):
        patch += "\n</style>"
    if "</body>" not in tail:
        patch += "\n</body>"
    return html + patch + "\n</html>"
