---
name: serve-browser
description: Act as the offline browser's rendering model — start it if needed, then answer its page requests in a loop until the reader stops browsing. Use when asked to serve, run, or write pages for the offline browser, or to browse the imagined web.
---

# Serving the offline browser

The browser at `http://127.0.0.1:8765` has no model of its own: with
`HLG_LLM=claude` every page it wants is parked in a queue and waits for a Claude
session to write it.

Serving happens in **`page-writer` subagents**, not in this conversation. Two
reasons, both about latency. A page's brief is self-contained, so the growing
history of this conversation is pure overhead on every request — a fresh, small
context stays fast from page one to page fifty. And the subagent can run on a
faster model than whatever this session happens to be, which matters more than
anything else here: the reader stares at a masthead until the whole page lands,
so output speed *is* the experience.

Spawn **two** of them. They pull from one queue and the browser ranks it, so the
second writer is what turns a hover into a page that already exists by the time
it is clicked, and what keeps the reader served while the first one is rotating
out. Two is the default; a third is for a genuinely busy reader.

## Start

1. `browser_status`. If it isn't running, `start_browser`. If it reports a
   provider other than `claude`, say so — pages are being written by that
   instead, and the fix is restarting it with `HLG_LLM=claude`.

2. **Pick the model.** Whatever the user named when invoking this skill —
   `/serve-browser sonnet`, `/serve-browser haiku`, `/serve-browser opus`. Pass
   it as the Agent tool's `model`. If they named nothing, omit `model` entirely
   so the subagent inherits this session's — never substitute a default of your
   own. If they ask which to use: Sonnet is the sweet spot, Haiku is fastest and
   thinner, Opus is the slowest by a wide margin and the difference shows up as
   dead time, not obviously better pages.

3. Spawn **two**, in one message so they start together, in the background so
   the user can keep talking to you:

   ```
   Agent(subagent_type: "page-writer",
         model: <only if the user named one>,
         run_in_background: true,
         description: "serve offline browser",
         prompt: "Serve the offline browser. Loop on next_request and write
                  every brief that comes back, until it reports idle about five
                  times in a row.")
   ```

   Identical prompts. They need no coordination and must not be given any: the
   browser hands each request to exactly one writer, so two loops on one queue
   share the work by pulling from it.

4. Tell the user the browser URL, which model is serving, and that they should
   start typing in the omnibox. One or two lines — they want to browse, not read.

Then stay out of the way. The subagents serve; you don't poll them, don't
double-serve alongside them, and don't relay their pages. Their completion
notifications arrive on their own.

## When it comes back

A `page-writer` retires itself after about eight requests, because by then its
context is mostly pages it has already delivered and every new one is read
against all of them. They come back one at a time, not together. Its final line
says which it was:

- **`rotating after N requests`** — it hit the ceiling, not the end of the
  reading. Spawn a replacement immediately, same model, same prompt, and say
  nothing to the user beyond a word that serving continues. The other writer
  covers the gap; keep the pair at two.
- **idle, or the user said stop** — the reader has stopped. Let the other one
  finish its own idles rather than replacing this one, and when both are back,
  say serving is over.
- **anything else** — report what it said. A worker that died mid-page leaves a
  reader on a half-written one; the browser takes an unanswered request back
  after a couple of minutes, and a replacement writer is what picks it up.

If the Agent tool isn't available in this session, serve inline instead: run the
loop yourself exactly as [the page-writer definition](../../agents/page-writer.md)
describes it, and follow every rule in it. Serving inline is the risky path,
because unlike the subagent you *do* have WebSearch, WebFetch, other servers' MCP
tools and the filesystem — and having them changes nothing. Use only
`next_request`, `write_page`, `decline_request` and `browser_status` until you
stop serving.

## While it serves

- `visit` commissions a URL nobody asked for. It returns immediately and the
  requests it creates go into the same queue, so a writer picks them up like any
  other. `rendered_page` reads back what one became — that's how you inspect a
  page without taking over the serving loop.
- Hovering a link now commissions the page behind it, ranked below anything a
  reader is watching. That is tokens spent on pages nobody may read; if the user
  would rather not, turn **prefetch** off in the browser's settings.
- A busy reader, or a faster model mid-session: spawn another `page-writer`.
  They pull from one queue, so they serve in parallel without coordination.
- To stop early, tell the user to say so, or stop the background agents.

## No external sources, none at all

Pages come from Claude's own imagination and what it already knows, and from
nothing else. **Nothing is fetched, searched, looked up or checked** — this is
the premise, not a side rule, and it binds whoever is writing pages: the
subagent, or you if you are serving inline. Off, by name:

- **No WebSearch, no WebFetch**, no browsing or crawling tool of any kind.
- **No MCP tool from any other server** — search connectors, docs/Drive/Gmail
  connectors, scrapers, indexes, caches. Being an MCP tool rather than
  `WebSearch` is not a loophole: if it can return something the model did not
  already know, it is banned.
- **No Read, Grep, Glob or Bash**, including on this repository. The project's
  own files are not a source for a page either.
- **No subagent** spawned to fetch or check something on the writer's behalf.

No exception for a statistic, a name, a date, a price, a version number, a
query that looks like it has a right answer, or a domain you know is real. The
`page-writer` agent is given four tools and nothing else precisely so this
cannot happen by accident; when you serve inline you have the whole toolbelt and
have to hold the line yourself.

A page built from real sources is the one page this browser must never serve.
The gap between what a model *thinks* `instagram.com` is and what it actually is
is the entire product.

If you catch a served page that reads too accurate to be invented — real
follower counts, a summary that is simply correct, results that match the actual
web — treat it as a bug: say so, and re-serve the URL with a fresh writer.

Two things enforce this rather than ask for it:

- **The hook.** [`.claude/hooks/no_external_sources.py`](../../hooks/no_external_sources.py)
  runs on `PreToolUse` and denies WebSearch, WebFetch and every non-`offline-browser`
  MCP tool while a worker is attached to the browser. Every attempt lands in
  `data/lookups.log`. If it blocks something you legitimately need, stop serving
  first — don't reach for `HLG_ALLOW_LOOKUPS=1` mid-session.
- **The audit.** `python tools/audit_lookups.py` scans this project's transcripts
  and reports any session that both wrote pages and looked something up. Run it
  if a page ever looks suspiciously correct.
