"""Models don't hand you clean HTML.

Qwen3 emits <think> blocks, most models wrap output in ```html fences, and some
chat about what they're about to do. This strips all of that out of a token
stream without ever holding the stream up.

It also enforces the one rule the whole design rests on: the model writes the
body, theme.py writes the stylesheet. A <style> block, or the document furniture
around it, is dropped here rather than trusted not to arrive -- a prompt is a
request, and this is the part that doesn't have to be.
"""

from __future__ import annotations

import html as html_mod
import re

# (opener, closer) pairs dropped from the stream, opener through closer.
_REGIONS = (
    ("<think>", "</think>"),
    ("<thinking>", "</thinking>"),
    ("<style", "</style>"),
    ("<head", "</head>"),
    # Document furniture: short, so the closer is just the end of the tag.
    ("<!doctype", ">"),
    ("<html", ">"),
    ("</html", ">"),
    ("<body", ">"),
    ("</body", ">"),
)
_OPENS = tuple(opener for opener, _ in _REGIONS)
_MAX_TAG = max(len(tag) for pair in _REGIONS for tag in pair)
# The boundary matters: without it `<head` swallows every `<header>` on the page.
_OPEN_RE = re.compile(
    "|".join(re.escape(opener) + ("" if opener.endswith(">") else r"(?![a-z0-9-])") for opener in _OPENS),
    re.I,
)
_CLOSE_RES = tuple(re.compile(re.escape(closer), re.I) for _, closer in _REGIONS)

_MAIN_RE = re.compile(r"<main\b", re.I)
_DOC_START_RE = re.compile(
    r"<(header|nav|main|article|section|aside|div|h1|h2|p|ul|ol|table|figure|form|canvas|center|font|hr|img)\b",
    re.I,
)
# Needs the newline: a bare ``` arriving on its own is an unfinished fence, and
# eating it early leaves the language tag behind as page text.
_FENCE_RE = re.compile(r"^\s*```[a-z]*[ \t]*\r?\n", re.I)
_PARTIAL_FENCE_RE = re.compile(r"^\s*`{1,3}[a-z]*[ \t]*\r?$", re.I)
_TRAILING_FENCE_RE = re.compile(r"\s*```[a-z]*\s*$", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.I | re.S)
_TAGS_RE = re.compile(r"<[^>]+>")

_HOLD = 8  # tail held back so a trailing ``` never reaches the page
_PROBE = 512  # opening characters kept for the caller's is-this-even-a-page test


def _dangling_prefix(text: str, tags) -> int:
    """Longest suffix of `text` that could still grow into one of `tags`."""
    lowered = text.lower()
    for k in range(min(len(text), _MAX_TAG - 1), 0, -1):
        tail = lowered[-k:]
        if any(tag.startswith(tail) for tag in tags):
            return k
    return 0


class HtmlCleaner:
    """push() returns what is safe to emit now; flush() returns the remainder."""

    def __init__(self) -> None:
        self._buf = ""
        self._region = -1  # index into _REGIONS, or -1 when not inside one
        self._started = False
        self._head = ""
        self._hold = ""
        self._probe = ""

    def _strip_regions(self, chunk: str, final: bool) -> str:
        self._buf += chunk
        out = []
        while True:
            if self._region >= 0:
                closer = _REGIONS[self._region][1]
                match = _CLOSE_RES[self._region].search(self._buf)
                if not match:
                    keep = 0 if final else _dangling_prefix(self._buf, (closer,))
                    self._buf = self._buf[len(self._buf) - keep :] if keep else ""
                    break
                self._buf = self._buf[match.end() :]
                self._region = -1
                continue

            match = _OPEN_RE.search(self._buf)
            if not match:
                keep = 0 if final else _dangling_prefix(self._buf, _OPENS)
                cut = len(self._buf) - keep
                out.append(self._buf[:cut])
                self._buf = self._buf[cut:]
                break

            # A boundary is only a boundary once something is on the other side
            # of it: `<head` at the end of the buffer may still become `<header`.
            if not final and match.end() == len(self._buf) and not match.group(0).endswith(">"):
                out.append(self._buf[: match.start()])
                self._buf = self._buf[match.start() :]
                break

            opener = match.group(0).lower()
            out.append(self._buf[: match.start()])
            self._buf = self._buf[match.end() :]
            self._region = _OPENS.index(opener)
        return "".join(out)

    def _strip_preamble(self, chunk: str, final: bool) -> str:
        """Drop a leading ```html fence, "Sure, here's the page:" chatter, and any
        masthead the model wrote in front of <main> despite being told not to.

        The page owes us one <main>. Anything before it is either preamble or a
        second copy of furniture the shell has already put on screen, so the
        stream starts there whenever there is a <main> to start at.
        """
        if self._started:
            return chunk
        self._head += chunk

        fence = _FENCE_RE.match(self._head)
        if fence:
            self._head = self._head[fence.end() :]
        elif not final and _PARTIAL_FENCE_RE.match(self._head):
            return ""

        opening = _MAIN_RE.search(self._head)
        if not opening:
            # No <main> yet. It may still be a few tokens away, so hold briefly
            # rather than commit to whatever came first.
            if not final and len(self._head) < 400:
                return ""
            opening = _DOC_START_RE.search(self._head)
        if not opening:
            self._started = True
            out, self._head = self._head, ""
            return out

        match = opening
        self._started = True
        out, self._head = self._head[match.start() :], ""
        return out

    @property
    def probe(self) -> str:
        """The opening of the answer, for deciding whether it is a page at all.

        Deliberately taken between the two filters. Reading what push() returns
        would be too late -- _strip_preamble holds up to 400 characters waiting
        for a <main> that may still arrive, so the caller's prose test could not
        fire until roughly twice the threshold it was written for. Reading the
        raw stream would be too early, because a reasoning model opens with a
        <think> block, which is prose and would fail the test every time.

        So: after <think> and <style> are gone, before the wait for <main>. The
        opening fence goes too, or ```html would read as prose on its own.
        """
        return _FENCE_RE.sub("", self._probe.lstrip(), count=1)

    def push(self, chunk: str) -> str:
        stripped = self._strip_regions(chunk, False)
        if len(self._probe) < _PROBE:
            self._probe += stripped
        text = self._hold + self._strip_preamble(stripped, False)
        if len(text) <= _HOLD:
            self._hold = text
            return ""
        self._hold = text[-_HOLD:]
        return text[:-_HOLD]

    def flush(self) -> str:
        tail = self._hold + self._strip_preamble(self._strip_regions("", True), True)
        self._hold = ""
        return _TRAILING_FENCE_RE.sub("", tail)


def _text_of(match) -> str:
    if not match:
        return ""
    inner = _TAGS_RE.sub("", match.group(1))
    return html_mod.unescape(re.sub(r"\s+", " ", inner)).strip()[:160]


def extract_title(html: str) -> str:
    return _text_of(_TITLE_RE.search(html or ""))


def extract_heading(html: str) -> str:
    """The page's own heading. The model writes no <title> any more, so this names the tab.

    Called on the model's <main> alone -- the masthead is Python's and lives
    outside it -- so the first heading here is the page's own, and a <header>
    inside an <article> is exactly where a headline is supposed to be.
    """
    body = html or ""
    return _text_of(_H1_RE.search(body)) or _text_of(_H2_RE.search(body))


# Block elements worth balancing before Python's own footer follows the model's
# output. An unterminated <script> or <style> would swallow the footer whole; an
# unterminated <main> would nest it inside the content column.
_BALANCE = ("script", "style", "main", "article", "section", "aside")
_TAG_SCAN_RE = re.compile(rf"<(/?)({'|'.join(_BALANCE)})\b[^>]*>", re.I)
_RAW_TEXT = ("script", "style")


def close_fragment(body: str) -> str:
    """Closers for whatever the model left open, innermost first."""
    stack: list[str] = []
    for match in _TAG_SCAN_RE.finditer(body or ""):
        closing, tag = bool(match.group(1)), match.group(2).lower()
        if stack and stack[-1] in _RAW_TEXT:
            # Inside a script or style, markup is just text -- a "</main>" in a
            # JavaScript string is not a tag, and must not pop the stack.
            if closing and tag == stack[-1]:
                stack.pop()
            continue
        if not closing:
            stack.append(tag)
        elif tag in stack:
            while stack and stack.pop() != tag:
                pass  # an unclosed inner element is closed by its parent closing
    return ("\n" + "".join(f"</{tag}>" for tag in reversed(stack))) if stack else ""


