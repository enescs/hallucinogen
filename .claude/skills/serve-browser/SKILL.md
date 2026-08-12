---
name: serve-browser
description: Act as the offline browser's rendering model — start it if needed, then answer its page requests in a loop until the reader stops browsing. Use when asked to serve, run, or write pages for the offline browser, or to browse the imagined web.
---

# Serving the offline browser

The browser at `http://127.0.0.1:8765` has no model of its own: with
`HLG_LLM=claude` every page it wants is parked in a queue and waits for a Claude
session to write it.

Serving happens in a **`page-writer` subagent**, not in this conversation. Two
reasons, both about latency. A page's brief is self-contained, so the growing
history of this conversation is pure overhead on every request — a fresh, small
context stays fast from page one to page fifty. And the subagent can run on a
faster model than whatever this session happens to be, which matters more than
anything else here: the reader stares at a masthead until the whole page lands,
so output speed *is* the experience.

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

3. Spawn it, in the background so the user can keep talking to you:

   ```
   Agent(subagent_type: "page-writer",
         model: <only if the user named one>,
         run_in_background: true,
         description: "serve offline browser",
         prompt: "Serve the offline browser. Loop on next_request and write
                  every brief that comes back, until it reports idle about five
                  times in a row.")
   ```

4. Tell the user the browser URL, which model is serving, and that they should
   start typing in the omnibox. One or two lines — they want to browse, not read.

Then stay out of the way. The subagent serves; you don't poll it, don't
double-serve alongside it, and don't relay its pages. Its completion
notification arrives on its own.

## When it comes back

A `page-writer` retires itself after about a dozen requests, because by then its
context is mostly pages it has already delivered and every new one is read
against all of them. Its final line says which it was:

- **`rotating after N requests`** — it hit the ceiling, not the end of the
  reading. Spawn a replacement immediately, same model, same prompt, and say
  nothing to the user beyond a word that serving continues. The queue holds
  while there's no worker, so the gap costs a moment, not a page.
- **idle, or the user said stop** — serving is over. Say so plainly.
- **anything else** — report what it said. A worker that died mid-page leaves a
  reader on a half-written one, and the fix is usually another worker.

If the Agent tool isn't available in this session, serve inline instead: run the
loop yourself exactly as [the page-writer definition](../../agents/page-writer.md)
describes it, and follow every rule in it — especially that you look nothing up
while serving.

## While it serves

- `visit` commissions a URL nobody asked for. It returns immediately and the
  requests it creates go into the same queue, so the subagent picks them up like
  any other. `rendered_page` reads back what one became — that's how you inspect
  a page without taking over the serving loop.
- More readers, or a faster model mid-session: spawn a second `page-writer`.
  They pull from one queue, so two serve in parallel without coordination.
- To stop early, tell the user to say so, or stop the background agent.

## Never look anything up

While serving, **nothing is fetched** — not to check what the real site looks
like, not for a statistic, not for a name or a date. The `page-writer` agent has
no tools that could, which is deliberate: it is the premise, not a side rule. A
page built from search results is the one page this browser must never serve.
The gap between what a model *thinks* `instagram.com` is and what it actually is
is the entire product.
