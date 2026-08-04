# Offline Browser

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
                       ├─ app   ─→ held back, then rendered whole and playable
                       └─ search─→ results invented, then rendered as a SERP
```

## Quick start

```bash
python3 setup.py           # venv, dependencies, Ollama, and a Qwen sized to your GPU
.venv/bin/python run.py    # then open http://127.0.0.1:8765
```

The wizard is happy to be interrupted — everything it does is also a button in
the browser's own setup panel, which opens by itself when something is missing.

Want to see the thing move before committing to a 5 GB download:

```bash
OB_MOCK=1 .venv/bin/python run.py     # canned pages, no model involved
```

Doing it by hand instead:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen3:8b
.venv/bin/python run.py
```

## Speed

A page is a few thousand tokens, so tokens-per-second is the whole experience.

- **GPU**: Ollama offloads automatically. `GPU layers` in settings maps to
  `num_gpu` — leave it at `-1` unless you want to force every layer on with `99`.
  The health dot's tooltip tells you how much of the model is actually in VRAM.
- **Keep loaded**: `keep_alive` defaults to `30m`, so the model stays resident
  and page two doesn't pay for a cold start. The server also warms it on boot.
- **Page depth**: the biggest lever. *Quick* pages arrive in a fraction of the
  time *Rich* ones take, because it's just fewer tokens.
- **Prefetch**: resting the pointer on a link starts writing that page. A real
  navigation cancels every guess, and if you click the link being guessed, it
  joins that job instead of starting a second one — a local model generates one
  thing at a time, and racing it against itself only makes both slower.
- **Memory**: every generated page is kept in `data/pages/`, so revisiting a URL
  gets you the same place. `regenerate` overrides it.

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
**app mode**: more tokens, and rendered only once complete, so a half-written
game never runs.

## Nothing reaches out

The only outbound connection in the whole program is to Ollama on localhost, plus
whatever the setup wizard downloads when you press its buttons. Generated pages
are locked down harder than that: a strict CSP (`default-src 'none'`,
`connect-src 'none'`) and a guard script that replaces `fetch`, `XMLHttpRequest`,
`WebSocket` and `window.open` before any page script runs. Links and form
submits are intercepted and handed back to the chrome as navigation, so a click
inside an invented page can only ever produce another invented page.

## Settings

| setting | what it does |
|---|---|
| Model | any tag Ollama has; a Qwen is picked automatically if the configured one is missing |
| Web era | Modern, Web 1.0, Brutalist, Terminal, Magazine — the design brief every page is written against |
| Page depth | Quick / Standard / Rich |
| Temperature | how far it wanders; defaults to 1.0 because a predictable web is a boring one. Game and tool code is generated below this cap — JavaScript that throws isn't a surprise, it's a broken page |
| Token ceiling | hard cap; depth sets the actual target |
| Context | `num_ctx` |
| GPU layers | `num_gpu`, `-1` for automatic |
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
server/
  app.py            routes; the page stream is server-sent events
  generator.py      URL → site profile → page/app/search, retries, fallback
  ollama.py         the only file that talks to Ollama
  prompts.py        what the web is told to be
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
