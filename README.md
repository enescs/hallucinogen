# Hallucinogen

A browser for a web that does not exist.

There is no internet here, no cache of real pages, no search index, no tools that
go and look something up. Type a domain and a local model writes the page it
thinks that domain would serve. Click a link on that page and it writes the next
one. Search for something and it invents the results, then invents the sites
behind them.

Typing `instagram.com` does not get you Instagram. It gets you what the model
thinks Instagram looks like — invented users, invented posts, invented counts.
That gap is the whole point.

```
  omnibox ─→ URL ─→ site profile (once per domain, remembered)
                       │
                       ├─ page  ─→ streamed in as it's written
                       ├─ app   ─→ streamed, but its script lands in one piece
                       └─ search─→ results invented, then rendered as a SERP
```

## Quick start

Python 3.10 or newer, and around 5 GB of disk for a model. Nothing else — the
dependencies are three packages and a local Ollama.

Linux and macOS:

```bash
python3 setup.py           # venv, dependencies, Ollama, and a Qwen sized to your GPU
.venv/bin/python run.py    # then open http://127.0.0.1:8765
```

Windows, in PowerShell — the same wizard, and the same browser:

```powershell
python setup.py
.venv\Scripts\python.exe run.py
```

Only three things differ, and all three are the platform rather than the
program: the venv keeps its interpreter in `Scripts` instead of `bin`, an
environment variable is set rather than prefixed, and Ollama arrives as an
installer from [ollama.com/download](https://ollama.com/download) rather than as
a shell script the wizard can run for you. Everything after that is identical.

The wizard is happy to be interrupted — everything it does is also a button in
the browser's own setup panel, which opens by itself when something is missing.

Want to see the thing move before committing to a 5 GB download:

```bash
HLG_MOCK=1 .venv/bin/python run.py                     # canned pages, no model involved
```
```powershell
$env:HLG_MOCK="1"; .venv\Scripts\python.exe run.py    # the same, in PowerShell
```

Or skip the local model entirely and let Claude write the pages — see
[Claude as the model](#claude-as-the-model).

Doing it by hand instead:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen3:8b
.venv/bin/python run.py
```

```powershell
python -m venv .venv; .venv\Scripts\pip.exe install -r requirements.txt
# install Ollama from https://ollama.com/download, then:
ollama pull qwen3:8b
.venv\Scripts\python.exe run.py
```

## Speed

A page is a few thousand tokens, so tokens-per-second is the whole experience.
Which means the fastest token is the one nobody has to generate — and most of
the work here is deciding what the model doesn't have to write.

**The model writes one thing: the `<main>` element.** Everything around it —
doctype, head, stylesheet, masthead, nav, footer — is built by `theme.py` from
the site profile, instantly, and is byte-for-byte identical on every page of a
site. It used to be regenerated per page from an instruction to *"repeat the
same header every time"*, which cost tokens and drifted anyway. Roughly 40% of
every document was a `<style>` block alone, restated from scratch at 45 tokens a
second. A `<style>` block or a second masthead that turns up regardless is
stripped from the stream rather than trusted not to arrive.

The rest, roughly in order of how much they're felt:

- **Effort** — one setting, from *Minimal* to *Full*, driving everything that
  costs tokens: page length, search result count, snippet length, and the
  ceiling for games. Turn it down and the whole browser gets faster at once.
- **Search streams too.** The search page goes on screen before a token is generated,
  with the query in the box — and then each result appears as it is written. The
  JSON used to have to be complete before any of it could render, which meant
  twenty seconds on an empty search engine at *Normal* effort, most of it after
  the first result had already been decided. A result is finished the moment its
  closing brace lands, so that is when it goes on screen. The schema bounds the
  array to exactly the number of results that will be rendered — asking in prose
  for eight and being handed ten meant paying for two nobody would ever see, and
  the grammar is the only thing that actually stops the model. Related searches
  left the schema altogether: eight three-word phrases are ~50 tokens spent at
  the one moment the reader is doing nothing but waiting for the page to end,
  and they are formulaic enough to stamp out locally. The summary sentence stays
  first, because at ~25 tokens it is the fastest thing that can appear at all.
- **The page is only asked for what can't be built.** Two rules used to
  contradict each other — *"start with `<header>`"* against *"nothing before
  `<main>`"* — and the masthead the model wrote to satisfy the first was thrown
  away on arrival by the filter enforcing the second. The further-reading block
  at the foot of every page was likewise both asked for and generated locally.
  Between them that was a fifth of a page at *Light* effort, generated and
  discarded or generated twice. The last surviving half of that contradiction
  was a line sitting under the site profile — *"repeat that exact header and
  nav, and a matching footer, on every page"* — which outlived the rule it came
  from and went on buying a masthead per page that nothing ever showed. The
  profile handed over the site's nav as an array of `{label, href}`, too, which
  reads as a thing to render; the sections are named as link destinations now,
  which is the only part the model has any use for.
- **Pictures are drawn here, not written.** `img.py` turns alt text into an SVG
  — and it has always known how to draw logos, charts, maps, portraits and
  screenshots, while the prompt was telling the model those "come out better as
  inline `<svg>` you draw yourself". A hand-drawn chart is 300–800 tokens
  against fifteen for `<img alt="bar chart of quarterly revenue">`: at 45 tokens
  a second, one decoration could cost eighteen seconds and a third of the budget
  for the whole page. Naming the kind in the alt text is what picks the drawing,
  so the prompt now says that, and reserves hand-drawn SVG for the rare thing
  none of the motifs covers.
- **The prompts are ordered so the model can skip re-reading them.** llama.cpp
  matches its prompt cache from token zero, so a prefix is reused only up to the
  first byte that differs. Roughly 1,100 tokens of hard rules were identical in
  the page prompt and the app prompt all along — but a one-line *"this URL
  serves software"* sat in front of them, so every switch between a page and a
  game re-evaluated the lot. What varies goes last now.

  The same prefix runs in front of the other two calls as well, which is where
  most of it was being lost. There is one cache and four kinds of prompt, and a
  site profile that opened with only the shared *sentence* — 57 tokens of a
  possible 1,132 — left the cache holding its own 345 tokens and nothing else.
  The page that followed it, the very page that profile had just been fetched
  *for*, then re-evaluated all 1,492 of its own from cold. Same for a search,
  which is the thing a reader does immediately before opening a page. Both are
  bounded by a JSON schema, which Ollama compiles to a grammar, so the format is
  enforced by the decoder rather than by the prose — which is what makes it safe
  to hand them a thousand tokens of rules about writing HTML and then one
  paragraph taking it back. All four now share 1,132 tokens, and the short calls
  pay nothing for the privilege: by the time either runs, the prefix is already
  in the cache.
- **The cache is warm before the first reader.** Loading the weights on boot was
  only ever half a cold start; the other half is the ~1,100 tokens of rules in
  front of every page, and warming the model with *"hi"* left the prompt cache
  empty for whoever arrived first to fill. The warm-up sends the shared prefix
  itself now, in the same position it will occupy on every later call, and asks
  for a single token — it is the prompt being evaluated that matters, not the
  answer.
- **A bad answer is cut off early.** A model that opens with an apology or a
  question isn't going to recover, and it used to run to the token ceiling before
  anyone noticed. Prose instead of markup is spotted within about 200 characters,
  which spares ~90% of a generation already going nowhere. The test is taken
  between the two stream filters, which is the only place it works: the filter
  that strips `<think>` blocks has to run first, or a reasoning model's opening
  paragraph fails the test every time — but the filter that waits up to 400
  characters for a `<main>` has to run after, or the check can't fire until long
  past the threshold it was written for. What the cleaner is still holding when
  the check fires is dropped, rather than flushed: it is more of the same
  apology, and letting it out put the refusal into the page, where the rule that
  gives a stray fragment its missing `<main>` would wrap it, the further-reading
  block would pad it past the is-this-a-page test, and the answer nobody wanted
  would be cached as the page for that URL.
- **The site profile** is the one call that blocks a page before a word of it can
  be written, so it asks for as little as it can: no colours (they're derived
  from the domain), one sentence of description, a low ceiling. It happens once
  per domain, and it's warmed speculatively while you're still typing the address
  or reading a results page — so by the time you press enter it's usually done.
  Whoever asks for a domain first starts it; everyone else joins that job.

  And when it isn't done, the page no longer waits on all of it. Colours are
  derived from the domain, not from the profile, so the stylesheet is knowable
  before the model has been asked anything — the head and the styled empty page
  under it go out first, and only the masthead, which genuinely needs the site's
  name, waits. On a domain nobody has visited, that call *was* the wait, and it
  was spent on a blank tab.
- **Games stream too.** App pages used to be held back entirely and rendered only
  once complete, which meant two minutes of nothing. Now the page furniture
  paints as it arrives and only the `<script>` waits, so a half-written game
  still never runs.
- **GPU**: Ollama offloads automatically. `GPU layers` in settings maps to
  `num_gpu` — leave it at `-1` unless you want to force every layer on with `99`.
  The health dot's tooltip tells you how much of the model is actually in VRAM,
  and it is the first thing to check when pages are slow: **partial offload
  costs more than every other setting on this page put together.** `qwen3:8b`
  needs about 6.9 GB with an 8k context, so on an 8 GB card it fits with nothing
  to spare. Three environment variables on the Ollama daemon buy back several GB
  of that by shrinking the KV cache, which is often the difference — and since
  that is worth more than every other setting on this page put together, it is a
  button in the setup panel (**Free up video memory**) rather than a paragraph
  here. It writes a systemd drop-in and restarts Ollama, so it needs admin
  rights; without them the panel hands you the same thing to paste:

  ```bash
  systemctl edit ollama       # or export these before `ollama serve`
  # Environment="OLLAMA_FLASH_ATTENTION=1"
  # Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
  # Environment="OLLAMA_NUM_PARALLEL=1"
  ```

  The last one is the largest and the least obvious. Ollama sizes the KV cache
  at `num_ctx` **times** that number, and left to itself it will pick 4 when it
  thinks there is room — turning an 8k context into 32k of cache, which is the
  likeliest single reason a model that ought to fit ends up half on the GPU. It
  costs a second time, too: requests round-robin across those slots and each one
  only remembers what went through it, so the ~1,100-token prefix in front of
  every page misses three times in four. One reader, one model, one page at a
  time — the other three slots were never going to be used.

  If it still won't fit, take a size down. `qwen3:4b` is about twice the speed
  and still holds a page together.
- **Keep loaded**: `keep_alive` defaults to `30m`, so the model stays resident
  and page two doesn't pay for a cold start. The server also warms it on boot —
  under the exact options the first page will ask for, because Ollama keys a
  loaded runner on `num_ctx`, `num_gpu` and `num_batch`. Warming with any of
  them different loaded the model twice: once on boot, and again from cold the
  moment a real page disagreed with it, which is precisely the cold start the
  warm-up exists to prevent.
- **The screen keeps up with the model, not with every token.** Tokens arrive
  far faster than a display refreshes, and each `document.write` into the page
  frame is an incremental parse and a reflow — hundreds a second, for text
  nobody can read that quickly. They are batched to one write per animation
  frame, which paints exactly as often as the screen can show it and stops the
  chrome competing for the CPU that a partly-offloaded model is running on.
- **The server keeps up too.** A page record carries the whole document, so
  reading or writing one is a couple of hundred kilobytes of JSON on the same
  thread that is streaming somebody's tokens. Those go to a worker now, the
  records are written without the indentation that prettifies nothing, and the
  prefetch guards that only need to know *whether* a URL is remembered ask the
  filesystem instead of parsing the page to find out.
- **Prefetch**: resting the pointer on a link starts writing that page. A real
  navigation cancels every guess — the page guesses and the profile guesses
  both, because Ollama has no priority queue and a ~220-token profile that
  started a moment before you pressed enter still runs its full length *in front
  of* the page you are now waiting for. If you click the link being guessed, you
  ride that job instead of starting a second one — a local model generates one
  thing at a time, and racing it against itself only makes both slower.

  Riding is the word. A guess used to throw its own output away and leave you to
  read the finished page off disk, which meant the one case prefetch exists for
  — clicking the link it guessed right — was the only case with nothing on
  screen until the very end: a status line, then a whole page in one piece. What
  a guess writes is kept now, so a click replays what it missed and then follows
  the rest live. Same total time, and the wait stops being a wait. That
  last fact is also why hovering buys two different things at two different
  speeds. The site profile is ~220 tokens and is wanted by every page on the
  domain, so a glance is evidence enough — even a wrong guess is banked for
  whoever goes there next. A whole page is ~2,300 tokens and takes the model off
  everything else while it runs, so it waits for the pointer to actually rest.
  One 220ms timer used to buy both, which meant sweeping across a paragraph of
  links committed the model to whichever one it crossed first.
- **Memory**: every generated page is kept in `data/pages/`, so revisiting a URL
  gets you the same place. `regenerate` overrides it.

`Context` stays at 8192 and shouldn't go lower: a game at *Full* effort needs
close to 8k tokens of prompt and output together, and a context that can't hold
it truncates the code with nothing to say why. The server clamps the token
ceiling to whatever the context can actually fit, so lowering it degrades pages
rather than breaking them.

## Going back

Back and forward never ask the model, and usually don't ask the server either.
Each history entry owns its own rendered document, and the last few stay alive:
going back shows the frame that is still there — same scroll position, same game
still in progress, same form still filled in. Only once an entry falls out of
that window is its document rebuilt from the HTML kept alongside it, and only if
that is gone too does it go back to the server, which still serves the same page
from `data/pages/`.

At no point does anything get imagined a second time.

## Favourites

The star in the omnibox (`Alt+S`) pins a page. Favourites sit in a strip under
the toolbar and on the new tab page — but the real point is that they survive
*forget every page*. Since there is no server to re-fetch from, clearing the
cache doesn't drop a copy, it destroys the place; a starred page and its site
profile are the exception and stay standing.

Smaller models are more fun than they sound. `qwen3:4b` writes a plausible news
site fast enough to browse casually; `qwen3:14b` writes better ones slower.

## What the model can and cannot make

It emits text, so it cannot hand you a PNG or an MP3. It can write code that
draws and code that makes sound, which covers more than it first appears:

| you want | what actually happens |
|---|---|
| a photograph | `<img alt="...">` with no src; the browser paints a deterministic SVG from the alt text |
| a logo, chart, diagram, map | inline `<svg>`, drawn by the model |
| a game | `<canvas>` plus real JavaScript — playable, with collision, scoring and a win state |
| a video | a canvas animation with a working timeline and transport, not a file |
| sound | WebAudio oscillators, muted until you click |

So `youtube.com` gets you a page whose player animates and makes noise, wrapped
in a title, a channel, a view count, comments and a sidebar of what's next. It
is not a video. It is a model's idea of one.

URLs that imply software — `/play/...`, `tetris.io`, a watch page — are built in
**app mode**: a higher token ceiling, and the `<script>` held back until it is
whole, so the page around a game paints as it is written but a half-finished game
never runs.

## Claude as the model

The browser doesn't care what writes its pages. `generator.provider()` picks a
module, that module answers `chat_stream` and `chat_json`, and everything else —
the site profile, the `<main>`-only contract, the retry on a refusal, the
streaming results page, the cache — is written against that interface and nothing
else. `mock.py` was the first proof of it. `claude.py` is the second:

```bash
HLG_LLM=claude .venv/bin/python run.py     # then open Claude Code in this project
```
```powershell
$env:HLG_LLM="claude"; .venv\Scripts\python.exe run.py
```

`.mcp.json` starts the MCP server with a bare `python`, because that is the one
name that resolves on both platforms — the venv's own interpreter is
`.venv/bin/python` here and `.venv\Scripts\python.exe` there, and a committed file
can only spell one of them. `mcp_server.py` hands itself over to the venv on the
way up, so whichever `python` starts it, the dependencies are the venv's. On the
machines where even a bare `python` resolves to nothing — a Linux without
`python-is-python3`, a macOS since 12.3 — `setup.py` notices and rewrites
`.mcp.json` to name the venv directly.

Ask Claude to serve the browser (`/serve-browser`), open the page, and type a
domain. The tab says what it always says; the difference is who is on the other
end of it.

What makes that awkward is direction. Ollama is a daemon this process calls.
Claude is a client that calls *us*, and MCP has no way for a server to ask its
client's model for text — `sampling` is exactly that request and Claude Code
doesn't implement it. So `claude.py` generates nothing. It parks the request in
`broker.py` and waits, and the page reaches Claude the only way MCP allows: as
the result of a tool call it made, or as an event pushed into its session.

Which gives two ways to run it, one env var apart:

- **Pull.** Claude calls `next_request`, which blocks until somebody browses,
  writes the page, calls `write_page`, and goes back for the next one. Works
  anywhere. Costs a loop that has to keep running.
- **Push.** `HLG_MCP_CHANNEL=1` makes the MCP server a
  [channel](https://code.claude.com/docs/en/channels-reference): the request is
  pushed into the session the moment you press enter, and no loop is involved.
  Channels are research preview, so the session needs
  `claude --dangerously-load-development-channels server:offline-browser`.

Both share one queue, so a request is handed over exactly once either way.

It also runs the other direction. `visit` commissions a URL nobody is looking at,
`rendered_page` reads back what it became — which is the browser as something to
drive rather than something to read.

### Nothing gets looked up

A local model has no tools, so "invent it" was never a rule anyone had to write
down — it was the only thing a model *could* do. Claude has a browser, a search
tool and a filesystem, and using any of them here would quietly turn an imagined
web into a researched one: real prices, real usernames, the actual layout of the
actual site. That is the one page this browser must never serve, because the gap
between what a model thinks `instagram.com` is and what it actually is *is* the
product.

So the ban is stated three times, at the three moments it could be forgotten: in
the MCP server's `instructions`, which land in the system prompt; in the
`/serve-browser` skill; and at the top of every single brief, above the rules,
where it cannot be scrolled past. Being wrong in an interesting way is the
output. Being right by looking it up is the failure.

The rule lives in the MCP layer rather than in `prompts.py` on purpose — those
prompts are shared with the local model, and spending tokens telling Ollama not
to use tools it does not have would be a tax on every page for nothing.

Three other things are genuinely different, and none of them are hidden:

- **A page takes a turn, not twenty seconds.** Roughly a minute, and the whole of
  it is quiet — no fans, no VRAM, no 8 GB of weights. What comes back is a page
  written by a frontier model rather than by 8B parameters running locally, which
  is the entire point of the exercise.
- **Nothing streams.** An answer arrives whole, as the argument of a tool call.
  `claude.py` cuts it back into deltas before handing it up, which is not theatre:
  every stage above it — the `<think>` and `<style>` filters, the app mode's
  held-back `<script>`, the results page's incremental JSON reader — is written
  against a stream, and a 60KB page arriving in one piece parses and reflows as
  one piece.
- **Hovering stops guessing.** Speculation assumes a model that is idle between
  pages and costs only electricity when it guesses wrong. A turn is neither, so
  `SPECULATIVE = False` and prefetch quietly steps aside. Which is also why
  `MAX_JOBS` never mattered until now: with a local model the queue was the
  bottleneck, and here the queue is the interface.

## Nothing reaches out

With Ollama, the only outbound connection in the whole program is to it on
localhost, plus whatever the setup wizard downloads when you press its buttons.

`HLG_LLM=claude` is the exception, and it is worth stating plainly: the page
prompts go to Anthropic, because that is where the model is. Everything else
holds — the browser still fetches nothing, still has no index, still invents
every page. It just invents them somewhere else. That path is the one where the
model has tools it could reach for, so the rule is spelled out at every layer it
passes through — the browser's own hard rules, the MCP server's instructions,
every request brief, and the `page-writer` agent, which is handed four tools and
no others: no external sources, none at all. No web search, no fetch, no MCP tool
from another server, no reading files, no subagent sent to check something. A
page assembled from real sources is the one page this browser must never serve.

Prose is a request, though, so two things back it up. `.claude/hooks/no_external_sources.py`
runs as a `PreToolUse` hook: while a worker is attached to the browser, WebSearch,
WebFetch and every non-`offline-browser` MCP tool are denied before they run, and
every attempt -- denied or allowed -- is appended to `data/lookups.log`. It gates
on serving rather than on this directory, so ordinary work on the repo is
unaffected; `HLG_STRICT_LOOKUPS=1` makes it unconditional and
`HLG_ALLOW_LOOKUPS=1` stands it down. And `python tools/audit_lookups.py` reads
back the other direction: it scans Claude Code's transcripts for this project and
reports any session that both wrote pages and reached outside, including sessions
from before the hook existed. Generated pages
are locked down harder than that: a strict CSP (`default-src 'none'`,
`connect-src 'none'`) and a guard script that replaces `fetch`, `XMLHttpRequest`,
`WebSocket` and `window.open` before any page script runs. Links and form
submits are intercepted and handed back to the chrome as navigation, so a click
inside an invented page can only ever produce another invented page.

## What this is, and what it isn't

It is a toy for one person on one machine. `run.py` binds `127.0.0.1` and there
is no account, no session, no authentication and no multi-user anything, because
nothing here was built on the assumption that a stranger could reach it.
`run.py` refuses to start on an address other machines can reach, and wants
`HLG_ALLOW_PUBLIC=1` before it will reconsider. Pointing this at a network turns
a private hallucination into something you are publishing to other people, and
every design decision above — no auth, no rate limit, no moderation, a model
told to commit to whatever it is handed — was made for the other case. The
variable exists so that doing it anyway is a decision rather than a typo.

Nothing on a generated page is real, and that includes the parts that look most
like they would be. Names, quotations, statistics, prices, dates, bylines and
counts are invented, in the specific sense that the model was *instructed* to
invent them rather than merely failing to look them up. A page about a real
company, a real place or a real person is fabrication about a real subject, and
if a screenshot of one leaves your machine, whatever it says is now something you
said. Every document the browser writes carries a `<meta name="generator">`
saying exactly that, because the page itself is forbidden to.

Typing a domain you recognise gets you the model's impression of it, built from
invented content. It is not that site, is not affiliated with it, and is not
endorsed by whoever owns it. That gap is the entire point of the project and it
stops being funny the moment anyone presents one of these as genuine.

The browser does not try to remove the model's own judgement about what it will
write. There is a rule near the top of `prompts.py` telling it never to bail out
on a URL it cannot make sense of, and that rule is about *unfamiliarity* — a
model that answers `qux.zone/7` with a clarifying question has left you looking
at an empty tab, which is the one failure this thing cannot have. It is scoped to
that and says so. What you point it at is a model you installed yourself, running
on your own hardware; it arrives with whatever its publisher gave it, and that is
between the two of you. Please use it somewhere legal.

## Settings

| setting | what it does |
|---|---|
| Model | any tag Ollama has; a Qwen is picked automatically if the configured one is missing |
| Web era | Modern, Web 1.0, Brutalist, Terminal, Magazine — a real stylesheet each, plus what that era changes about the writing |
| Effort | Minimal / Light / Normal / Full — scales page length, search results, snippet length and the game ceiling together |
| Temperature | how far it wanders; defaults to 1.0 because a predictable web is a boring one. Game and tool code is generated below this cap — JavaScript that throws isn't a surprise, it's a broken page |
| Token ceiling | hard cap; effort sets the actual target, and the context window caps both |
| Context | `num_ctx` |
| GPU layers | `num_gpu`, `-1` for automatic |
| Prompt batch | `num_batch`. Prompt tokens read per pass, and a page carries ~1,500 of them. Defaults to 1024 — double Ollama's own — which halves the passes before the first word at the cost of a hundred megabytes or so of compute buffer. That used to be the wrong trade on the card that would notice; pinning `OLLAMA_NUM_PARALLEL` hands the same card back whole gigabytes, so it isn't any more. A settings file written before this keeps whatever it has |
| Keep model loaded | `keep_alive` |
| Prefetch on hover | speculative generation |
| Think first | reasoning models only; accurate, and a large latency tax |
| Remember pages | the fake web stays put between visits |

## Keys

`Alt+T` new tab · `Alt+W` close · `Alt+D` omnibox · `Alt+R` reload ·
`Alt+S` star · `Alt+←` / `Alt+→` back and forward · `Esc` stop

They are relayed out of the page frame too, so they still work while a game has
the keyboard.

## Layout

```
run.py              start the server
setup.py            terminal setup wizard
mcp_server.py       the browser over MCP — and, with HLG_LLM=claude, its model
server/
  app.py            routes; the page stream is server-sent events
  generator.py      URL → site profile → page/app/search, retries, fallback
  ollama.py         the only file that talks to Ollama
  claude.py         the backend that generates nothing and waits to be answered
  broker.py         requests parked for a worker that isn't a local model
  prompts.py        what the web is told to be
  theme.py          the stylesheet the model no longer writes, one per web era
  prefetch.py       speculative generation and its cancellation
  stream_filters.py strips <think> blocks and ``` fences from a live stream
  serp.py           the search engine's own page
  img.py            alt text → SVG
  fallback.py       the page of last resort
  store.py          settings, pages, site profiles, history, favourites — plain files in data/
  mock.py           canned provider, so the browser runs before a model does
public/
  index.html        the chrome
  app.js            tabs, omnibox, streaming render, wizard
  inject.js         runs inside every generated page: links, forms, images, lockdown
```

A page is never allowed to come back empty. If the model refuses, asks a
question, or answers with prose instead of a document, it gets one firm retry;
if that fails too, `fallback.py` builds a plain page for the URL so the tab
always has somewhere to be. Not knowing what a URL means is an invitation, not
an error.

## Licence

Apache 2.0 — see [LICENSE](LICENSE). Do what you like with it, including
commercially; keep the notice, and note that it comes with no warranty of any
kind and its authors are not liable for what it writes.

No model weights are shipped or bundled. Ollama and whatever model you pull
carry their own licences, which are between you and their publishers.
