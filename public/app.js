'use strict';

/* The chrome: tabs, omnibox, favourites, and the machinery that turns a stream
 * of HTML from a local model into something that behaves like a web page.
 *
 * Each history entry owns its own rendered document. Going back doesn't ask the
 * server and doesn't ask the model -- it shows the frame that is still there,
 * scroll position and running game included. Only once an entry falls out of the
 * live window is its document rebuilt from the HTML we kept. */

const NEW_TAB = 'about:newtab';
const LIVE_FRAMES = 5;   // rendered documents kept alive per tab
const KEEP_HTML = 25;    // entries that keep their HTML after losing their frame

const $ = (id) => document.getElementById(id);

const state = {
  tabs: [],
  activeId: null,
  seq: 0,
  inject: '',
  settings: {},
  meta: { styles: [], efforts: [], provider: 'ollama', mock: false },
  health: null,
  setup: null,
  bookmarks: [],
  hoverTimer: null,
  hoverSiteTimer: null,
  hoverUrl: '',
};

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

/* ------------------------------------------------------------------- boot */

async function boot() {
  state.inject = await fetch('/static/inject.js').then((r) => r.text()).catch(() => '');
  await Promise.all([loadSettings(), loadBookmarks()]);
  wire();
  createTab();
  refreshHealth();
  checkSetup();
  setInterval(refreshHealth, 20000);
}

async function loadSettings() {
  try {
    const data = await fetch('/api/settings').then((r) => r.json());
    state.settings = data.settings || {};
    state.meta = { styles: data.styles || [], efforts: data.efforts || [], provider: data.provider, mock: data.mock };
  } catch (err) {
    setStatus('Could not read settings from the browser server.');
  }
}

/* ------------------------------------------------------- entries and frames */

function newEntry(url) {
  return { url, title: '', html: '', mode: 'page', scroll: 0, frame: null, open: false, pending: '', flush: 0 };
}

function entryOf(tab) {
  return tab.entries[tab.index] || null;
}

function makeFrame() {
  const frame = document.createElement('iframe');
  frame.setAttribute('title', 'page');
  $('viewport').appendChild(frame);
  return frame;
}

function ensureFrame(tab, entry) {
  if (!entry.frame) {
    entry.frame = makeFrame();
    entry.open = false;
    if (entry.html) renderWhole(entry, entry.html);
  }
  return entry.frame;
}

function dropFrame(entry) {
  if (!entry.frame) return;
  discardPending(entry);
  entry.frame.remove();
  entry.frame = null;
  entry.open = false;
}

/* Keep the neighbourhood of the current entry alive; let the rest go. */
function evictFrames(tab) {
  const live = tab.entries
    .map((entry, i) => ({ entry, distance: Math.abs(i - tab.index) }))
    .filter((x) => x.entry.frame)
    .sort((a, b) => b.distance - a.distance);
  while (live.length > LIVE_FRAMES) dropFrame(live.shift().entry);

  tab.entries.forEach((entry, i) => {
    if (Math.abs(i - tab.index) > KEEP_HTML) entry.html = '';
  });
}

function dropForward(tab) {
  tab.entries.slice(tab.index + 1).forEach(dropFrame);
  tab.entries = tab.entries.slice(0, tab.index + 1);
}

function paintFrames() {
  state.tabs.forEach((tab) => {
    tab.entries.forEach((entry, i) => {
      if (entry.frame) entry.frame.classList.toggle('active', tab.id === state.activeId && i === tab.index);
    });
  });
}

function saveScroll(tab) {
  const entry = entryOf(tab);
  if (!entry || !entry.frame) return;
  try { entry.scroll = entry.frame.contentWindow.scrollY || 0; } catch (e) { /* not ours to read */ }
}

function restoreScroll(entry) {
  if (!entry.frame || !entry.scroll) return;
  try { entry.frame.contentWindow.scrollTo(0, entry.scroll); } catch (e) {}
}

/* -------------------------------------------------------------------- tabs */

function active() {
  return state.tabs.find((t) => t.id === state.activeId) || null;
}

function createTab(url) {
  const id = ++state.seq;
  const tab = {
    id,
    entries: [], index: -1,
    title: 'New tab', url: NEW_TAB,
    loading: false, controller: null,
    mode: 'page', cached: false, buffer: '', chars: 0,
    status: '', stats: '',
  };
  state.tabs.push(tab);
  state.activeId = id;
  if (url) navigate(tab, url);
  else showNewTab(tab);
  renderTabs();
  return tab;
}

function activate(id) {
  const previous = active();
  if (previous) saveScroll(previous);
  state.activeId = id;
  paintFrames();
  renderTabs();
  syncChrome();
  const tab = active();
  if (tab) restoreScroll(entryOf(tab) || {});
}

function closeTab(id) {
  const i = state.tabs.findIndex((t) => t.id === id);
  if (i === -1) return;
  const tab = state.tabs[i];
  stop(tab);
  tab.entries.forEach(dropFrame);
  state.tabs.splice(i, 1);
  if (!state.tabs.length) return void createTab();
  activate((state.tabs[i] || state.tabs[i - 1]).id);
}

function renderTabs() {
  const strip = $('tabstrip');
  strip.querySelectorAll('.tab').forEach((n) => n.remove());
  const plus = $('newTab');

  state.tabs.forEach((tab) => {
    const el = document.createElement('div');
    el.className = 'tab' + (tab.id === state.activeId ? ' active' : '');
    el.onclick = () => activate(tab.id);

    if (tab.loading) el.appendChild(Object.assign(document.createElement('span'), { className: 'spin' }));
    el.appendChild(Object.assign(document.createElement('span'), { className: 'label', textContent: tab.title }));

    const x = Object.assign(document.createElement('button'), { className: 'x', textContent: '×', title: 'Close (Alt+W)' });
    x.onclick = (e) => { e.stopPropagation(); closeTab(tab.id); };
    el.appendChild(x);

    strip.insertBefore(el, plus);
  });
}

function setTitle(tab, title) {
  tab.title = (title || tab.title || 'Untitled').slice(0, 80);
  const entry = entryOf(tab);
  if (entry) entry.title = tab.title;
  renderTabs();
  if (tab.id === state.activeId) document.title = tab.title + ' — Offline Browser';
}

/* --------------------------------------------------------- chrome plumbing */

function displayUrl(url) {
  if (!url || url === NEW_TAB) return '';
  try {
    const u = new URL(url);
    if (u.hostname === 'mirage.search') return u.searchParams.get('q') || url;
  } catch (e) { /* not a url, show it raw */ }
  return url;
}

function syncChrome() {
  const tab = active();
  if (!tab) return;
  if (document.activeElement !== $('address')) $('address').value = displayUrl(tab.url);
  $('back').disabled = tab.index <= 0;
  $('forward').disabled = tab.index >= tab.entries.length - 1;
  $('reload').textContent = tab.loading ? '×' : '⟳';
  $('reload').title = tab.loading ? 'Stop' : 'Reload (Alt+R)';
  $('bar').className = 'bar' + (tab.loading ? ' loading' : '');
  $('bar').style.opacity = tab.loading ? '1' : '0';
  setStatus(tab.status);
  $('stats').textContent = tab.stats;
  document.title = (tab.title || 'Offline Browser') + ' — Offline Browser';
  // Apps used to hide behind this while the whole document was buffered. They
  // stream now -- only their <script> is held back -- so there is nothing to
  // hide, and watching the page assemble beats watching a progress panel.
  $('build').hidden = true;
  syncStar();
}

function setStatus(text) {
  const tab = active();
  if (tab) tab.status = text || '';
  $('status').textContent = text || '';
}

function setStats(tab, text) {
  tab.stats = text || '';
  if (tab.id === state.activeId) $('stats').textContent = tab.stats;
}

/* ------------------------------------------------- writing into a page frame */

function framePrefix(url) {
  const origin = location.origin;
  const csp = [
    "default-src 'none'",
    `img-src ${origin} data: blob:`,
    "style-src 'unsafe-inline'",
    "script-src 'unsafe-inline' 'unsafe-eval' blob:",
    'font-src data:',
    "connect-src 'none'",
    "frame-src 'none'",
    "form-action 'none'",
  ].join('; ');

  return '<!doctype html><html><head><meta charset="utf-8">'
    + `<meta http-equiv="Content-Security-Policy" content="${csp}">`
    + `<base href="${origin}/">`
    + `<script>window.__OB__=${JSON.stringify({ url, origin })};<\/script>`
    + `<script>${state.inject}<\/script>`;
}

function openDoc(entry) {
  const doc = entry.frame.contentDocument;
  discardPending(entry);  // doc.open() wipes the document; anything queued was for the old one
  doc.open();
  doc.write(framePrefix(entry.url));
  entry.open = true;
}

/* Tokens arrive far faster than a screen refreshes, and every document.write is
 * an incremental parse and a reflow. Writing each one as it lands meant hundreds
 * of layouts a second for a page nobody can read that quickly -- work that comes
 * out of the same CPU the model is using whenever it isn't fully on the GPU.
 * One write per frame paints exactly as often as the display can show it. */
function writeDoc(entry, text) {
  if (!entry.open) openDoc(entry);
  entry.pending += text;
  if (!entry.flush) {
    entry.flush = requestAnimationFrame(() => { entry.flush = 0; flushDoc(entry); });
  }
}

function flushDoc(entry) {
  if (entry.flush) { cancelAnimationFrame(entry.flush); entry.flush = 0; }
  const text = entry.pending;
  entry.pending = '';
  if (!text || !entry.open || !entry.frame) return;
  try { entry.frame.contentDocument.write(text); } catch (e) { /* the frame went away */ }
}

/* Nothing half-written survives a restart or a dropped frame. */
function discardPending(entry) {
  if (entry.flush) { cancelAnimationFrame(entry.flush); entry.flush = 0; }
  entry.pending = '';
}

function closeDoc(entry) {
  flushDoc(entry);
  if (!entry.open) return;
  try { entry.frame.contentDocument.close(); } catch (e) { /* already gone */ }
  entry.open = false;
  try {
    const win = entry.frame.contentWindow;
    if (win && win.__obSweep) win.__obSweep();
  } catch (e) {}
}

function renderWhole(entry, html) {
  openDoc(entry);
  writeDoc(entry, html);
  closeDoc(entry);
  restoreScroll(entry);
}

function focusPage(tab) {
  // Games read the keyboard, so the page has to hold focus once it lands.
  if (tab.id !== state.activeId) return;
  const entry = entryOf(tab);
  if (!entry || !entry.frame) return;
  try { entry.frame.contentWindow.focus(); } catch (e) {}
}

/* -------------------------------------------------------------- navigation */

function stop(tab) {
  if (tab.controller) { tab.controller.abort(); tab.controller = null; }
  tab.loading = false;
}

/* Back and forward never regenerate. If the document is still alive it is simply
 * shown again -- same scroll, same game in progress. */
function go(delta) {
  const tab = active();
  if (!tab) return;
  const next = tab.index + delta;
  if (next < 0 || next >= tab.entries.length) return;

  stop(tab);
  saveScroll(tab);
  tab.index = next;

  const entry = entryOf(tab);
  if (entry.url === NEW_TAB) return void showNewTab(tab, false);

  tab.url = entry.url;
  tab.mode = entry.mode;
  tab.cached = true;
  setTitle(tab, entry.title || displayUrl(entry.url));

  if (entry.frame || entry.html) {
    ensureFrame(tab, entry);
    evictFrames(tab);
    paintFrames();
    restoreScroll(entry);
    setStatus('Kept in memory');
    setStats(tab, entry.html ? (entry.html.length / 1000).toFixed(1) + 'k chars' : '');
    syncChrome();
    focusPage(tab);
  } else {
    navigate(tab, entry.url, { push: false }); // fell out of memory; the server still has it
  }
}

async function navigate(tab, input, opts) {
  opts = opts || {};
  const push = opts.push !== false;

  stop(tab);
  /* Whatever the pointer was resting on, it isn't being guessed at any more --
   * the server cancels every speculation the moment anything actually goes. If
   * this stays set, hovering that same link again is a no-op, and a guess that
   * was cancelled halfway never gets started a second time. */
  clearTimeout(state.hoverTimer);
  clearTimeout(state.hoverSiteTimer);
  state.hoverUrl = '';
  if (input === NEW_TAB) return void showNewTab(tab, push);

  let entry;
  if (push) {
    saveScroll(tab);
    dropForward(tab);
    entry = newEntry(input);
    tab.entries.push(entry);
    tab.index = tab.entries.length - 1;
  } else {
    entry = entryOf(tab);
    if (!entry) {
      entry = newEntry(input);
      tab.entries.push(entry);
      tab.index = tab.entries.length - 1;
    }
    entry.url = input;
    entry.html = '';
    entry.scroll = 0;
    entry.open = false;
  }
  ensureFrame(tab, entry);
  evictFrames(tab);
  paintFrames();

  tab.loading = true;
  tab.buffer = '';
  tab.chars = 0;
  tab.cached = false;
  tab.mode = 'page';
  tab.url = input;
  setTitle(tab, displayUrl(input) || input);
  if (tab.id === state.activeId) {
    $('address').value = displayUrl(input);
    setStatus('Connecting…');
    setStats(tab, '');
    $('buildTail').textContent = '';
    $('buildCount').textContent = '';
  }
  syncChrome();

  const params = new URLSearchParams({ q: input });
  if (opts.from) params.set('from', opts.from);
  if (opts.text) params.set('text', opts.text);
  if (opts.fresh) params.set('fresh', '1');

  const controller = new AbortController();
  tab.controller = controller;
  const started = performance.now();

  try {
    const res = await fetch('/api/page?' + params, { signal: controller.signal, headers: { accept: 'text/event-stream' } });
    if (!res.ok || !res.body) throw new Error('the browser server answered ' + res.status);
    await readEvents(res.body, (type, data) => handle(tab, entry, type, data, { push, started }));
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    fail(tab, entry, { code: 'SERVER', message: String((err && err.message) || err), hint: 'Is run.py still running?' });
  } finally {
    if (tab.controller === controller) tab.controller = null;
    tab.loading = false;
    renderTabs();
    if (tab.id === state.activeId) syncChrome();
  }
}

async function readEvents(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let split;
    while ((split = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, split);
      buf = buf.slice(split + 2);

      let type = 'message';
      let data = '';
      raw.split('\n').forEach((line) => {
        if (line.startsWith('event:')) type = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      });
      if (!data) continue;
      try { onEvent(type, JSON.parse(data)); } catch (e) { /* skip a malformed frame */ }
    }
  }
}

function handle(tab, entry, type, data, ctx) {
  switch (type) {
    case 'meta': {
      entry.url = data.url;
      entry.mode = tab.mode = data.mode || 'page';
      tab.url = data.url;
      tab.cached = !!data.cached;
      setTitle(tab, data.title || data.domain);
      if (tab.id === state.activeId) {
        $('address').value = displayUrl(data.url);
        $('buildTitle').textContent = 'Building ' + data.domain;
      }
      setStatus(data.cached ? 'Serving a page you have already seen' : 'Imagining ' + data.domain + '…');
      syncChrome();
      break;
    }

    case 'mode': {
      entry.mode = tab.mode = data.mode || tab.mode;
      if (data.site && data.site.name && tab.id === state.activeId) $('buildTitle').textContent = 'Building ' + data.site.name;
      if (data.text) setStatus(data.text);
      syncChrome();
      break;
    }

    case 'status':
      setStatus(data.text || '');
      break;

    case 'restart': {
      // The model wandered off. Throw away what we have and take it again.
      tab.buffer = '';
      tab.chars = 0;
      discardPending(entry);
      entry.open = false;
      if (data.text) setStatus(data.text);
      break;
    }

    case 'chunk': {
      tab.chars += data.text.length;
      tab.buffer += data.text;                      // always kept, so back can rebuild
      // Apps stream too: the server holds their <script> back until it is whole,
      // so what arrives before it is page furniture and safe to paint.
      const stream = !tab.cached;
      if (stream) writeDoc(entry, data.text);

      if (tab.id === state.activeId) {
        setStats(tab, (tab.chars / 1000).toFixed(1) + 'k chars');
        if (!stream && !tab.cached) {
          $('buildTail').textContent = tab.buffer.slice(-420);
          $('buildCount').textContent = tab.chars.toLocaleString() + ' characters written';
        }
      }
      break;
    }

    case 'done': {
      entry.html = tab.buffer;
      const streamed = !tab.cached && entry.open;
      if (streamed) closeDoc(entry);
      else renderWhole(entry, tab.buffer);
      tab.buffer = '';

      tab.loading = false;
      tab.cached = !!data.cached;
      setTitle(tab, data.title);
      syncChrome();
      focusPage(tab);
      if (tab.mode === 'search') warmResultSites(entry);

      const ms = Math.round(performance.now() - ctx.started);
      const bits = [];
      if (data.cached) bits.push('from memory');
      else {
        bits.push(data.model || 'model');
        if (data.stats && data.stats.tokensPerSecond) bits.push(data.stats.tokensPerSecond + ' tok/s');
        bits.push((ms / 1000).toFixed(1) + 's');
      }
      bits.push((data.chars / 1000).toFixed(1) + 'k chars');
      setStats(tab, bits.join(' · '));
      setStatus(
        data.fallback ? 'The model would not commit — this is a stub page.'
          : data.truncated ? 'Hit the token ceiling; the tail was closed off.'
          : data.cached ? 'From memory' : 'Done'
      );
      break;
    }

    case 'error':
      fail(tab, entry, data);
      break;
  }
}

function fail(tab, entry, err) {
  tab.loading = false;
  tab.buffer = '';
  entry.html = errorHtml(err, entry.url);
  entry.open = false;
  ensureFrame(tab, entry);
  renderWhole(entry, entry.html);
  paintFrames();
  setTitle(tab, 'Not reachable');
  setStatus(err.message || 'Something went wrong');
  setStats(tab, err.code || '');
  syncChrome();
}

/* ------------------------------------------------------------ local pages */

function showNewTab(tab, push) {
  stop(tab);
  let entry;
  if (push !== false) {
    saveScroll(tab);
    dropForward(tab);
    entry = newEntry(NEW_TAB);
    tab.entries.push(entry);
    tab.index = tab.entries.length - 1;
  } else {
    entry = entryOf(tab) || newEntry(NEW_TAB);
    if (!tab.entries.length) { tab.entries.push(entry); tab.index = 0; }
  }
  entry.url = NEW_TAB;
  entry.open = false;

  tab.url = NEW_TAB;
  tab.mode = 'page';
  tab.cached = false;
  tab.loading = false;
  setTitle(tab, 'New tab');
  setStats(tab, '');
  setStatus(state.meta.mock ? 'Mock provider — canned pages, no model' : '');

  ensureFrame(tab, entry);
  entry.html = startPageHtml();
  renderWhole(entry, entry.html);
  evictFrames(tab);
  paintFrames();
  syncChrome();

  fetch('/api/history')
    .then((r) => r.json())
    .then((data) => {
      const list = (data.history || []).slice(0, 8);
      if (!list.length || !entry.frame) return;
      const doc = entry.frame.contentDocument;
      const holder = doc && doc.getElementById('recent');
      if (!holder) return;
      holder.innerHTML = '<h2>Where you have been</h2>' + list.map((h) =>
        `<a class="recent" href="${esc(h.url)}"><b>${esc(h.title)}</b><span>${esc(h.url)}</span></a>`).join('');
      entry.html = '<!doctype html>' + doc.documentElement.outerHTML;
    })
    .catch(() => {});
}

const SUGGESTIONS = [
  ['mirage.search', 'the search engine of a web that is not there'],
  ['cabinet.arcade/play/', 'something to play'],
  ['instagram.com', 'what the model thinks it looks like'],
  ['en.wikipedia.org/wiki/Antikythera_mechanism', 'an encyclopaedia entry'],
  ['news.ycombinator.com', 'an orange front page'],
  ['1997.geocities.com/aquarium', 'a personal site from 1997'],
  ['moon.gov/colony/permits', 'a government form'],
  ['recipes.forgotten.kitchen/borscht', 'dinner'],
];

function startPageHtml() {
  const chips = SUGGESTIONS.map(([url, note]) =>
    `<a class="chip" href="https://${esc(url)}"><b>${esc(url)}</b><span>${esc(note)}</span></a>`).join('');

  const favourites = state.bookmarks.length
    ? '<div id="favs"><h2>Favourites</h2>' + state.bookmarks.slice(0, 12).map((b) =>
        `<a class="recent" href="${esc(b.url)}"><b>★ ${esc(b.title)}</b><span>${esc(b.domain || b.url)}</span></a>`).join('') + '</div>'
    : '';

  return `<!doctype html><html><head><meta charset="utf-8"><title>New tab</title><style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:#0a0b10; color:#d8dbe6;
      font:14px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      display:flex; flex-direction:column; align-items:center; justify-content:center; padding:40px 20px; }
    h1 { font-size:44px; margin:0 0 6px; letter-spacing:-1.4px; font-weight:700;
      background:linear-gradient(92deg,#5b6bff,#b44bd8 55%,#ff7a59);
      -webkit-background-clip:text; background-clip:text; color:transparent; }
    .tag { color:#7d8399; margin-bottom:30px; font-size:13.5px; }
    form { width:min(620px,100%); display:flex; gap:8px; margin-bottom:34px; }
    input { flex:1; padding:13px 18px; border-radius:26px; border:1px solid #272a38; background:#12131b;
      color:#d8dbe6; font-size:15px; outline:none; }
    input:focus { border-color:#4a4f6b; box-shadow:0 0 0 3px rgba(139,123,255,.14); }
    button { padding:0 22px; border-radius:26px; border:0; font-size:14px; font-weight:600; cursor:pointer;
      color:#12101f; background:linear-gradient(92deg,#8b7bff,#b44bd8); }
    .grid { width:min(760px,100%); display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:9px; }
    .chip { display:block; padding:12px 14px; border:1px solid #21232f; border-radius:11px; text-decoration:none;
      transition:background .12s,border-color .12s; }
    .chip:hover { background:#12131b; border-color:#343850; }
    .chip b { display:block; color:#c8ccdb; font-weight:500; font-size:13px; overflow-wrap:anywhere; }
    .chip span { color:#5f6579; font-size:11.5px; }
    #recent, #favs { width:min(760px,100%); margin-top:34px; }
    #recent h2, #favs h2 { font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:#5f6579; margin:0 0 10px; }
    .recent { display:flex; justify-content:space-between; gap:16px; padding:7px 2px; text-decoration:none; }
    .recent:hover b { color:#fff; }
    .recent b { color:#a9aec2; font-weight:400; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .recent span { color:#474c60; font-size:11.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:45%; }
  </style></head><body>
    <h1>a web that isn't there</h1>
    <div class="tag">Every page you open from here is written on the spot. None of it exists.</div>
    <form action="https://mirage.search/search" method="get">
      <input name="q" placeholder="Search for anything, real or not" autofocus autocomplete="off" spellcheck="false">
      <button type="submit">Search</button>
    </form>
    <div class="grid">${chips}</div>
    ${favourites}
    <div id="recent"></div>
  </body></html>`;
}

function errorHtml(err, url) {
  const hint = err.hint ? `<p class="hint">${esc(err.hint)}</p>` : '';
  const wizard = (err.code === 'OFFLINE' || err.code === 'MODEL_NOT_FOUND')
    ? '<a class="btn" href="ob:setup">Open the setup wizard</a>' : '';
  return `<!doctype html><html><head><meta charset="utf-8"><title>Nothing came back</title><style>
    :root { color-scheme: dark; }
    body { margin:0; min-height:100vh; background:#0a0b10; color:#d8dbe6; display:grid; place-items:center;
      font:14px/1.65 system-ui,-apple-system,sans-serif; padding:30px; }
    .box { max-width:520px; }
    h1 { font-size:22px; margin:0 0 10px; }
    code { color:#8b7bff; font-size:12.5px; word-break:break-all; }
    p { color:#8f95a8; }
    .hint { border-left:2px solid #3a3f56; padding-left:12px; }
    .row { display:flex; gap:9px; margin-top:20px; flex-wrap:wrap; }
    .btn { display:inline-block; padding:9px 16px; border-radius:9px; text-decoration:none; font-size:13px;
      background:#191b26; border:1px solid #2a2e3d; color:#d8dbe6; }
    .btn:hover { border-color:#4a4f6b; }
  </style></head><body><div class="box">
    <h1>Nothing came back</h1>
    <p><code>${esc(url)}</code></p>
    <p>${esc(err.message || 'The page could not be written.')}</p>
    ${hint}
    <div class="row">
      <a class="btn" href="ob:retry">Try again</a>
      ${wizard}
      <a class="btn" href="ob:settings">Settings</a>
      <a class="btn" href="ob:home">New tab</a>
    </div>
  </div></body></html>`;
}

/* ------------------------------------------------ messages from a page frame */

window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || !data.__ob) return;

  let tab = null;
  for (const candidate of state.tabs) {
    if (candidate.entries.some((e) => e.frame && e.frame.contentWindow === event.source)) { tab = candidate; break; }
  }
  if (!tab) return;

  switch (data.type) {
    case 'navigate':
      if (data.newTab) createTab(data.url);
      else navigate(tab, data.url, { from: tab.url.startsWith('http') ? tab.url : '', text: data.text });
      break;

    case 'hover':
      queuePrefetch(tab, data.url, data.text);
      break;

    case 'unhover':
      clearTimeout(state.hoverTimer);
      clearTimeout(state.hoverSiteTimer);
      break;

    case 'title':
      if (data.title) setTitle(tab, data.title);
      break;

    case 'shortcut':
      shortcut(data);
      break;

    case 'action':
      if (data.action === 'retry') navigate(tab, tab.url, { push: false, fresh: true });
      else if (data.action === 'home') showNewTab(tab);
      else if (data.action === 'settings') openSettings();
      else if (data.action === 'setup') checkSetup(true);
      break;
  }
});

/* A site profile is ~220 tokens against a page's thousands, so it is worth
 * guessing at on much weaker evidence than a hover: a half-typed address, a
 * domain sitting in a search result. The server dedupes by domain, so guessing
 * the same one twice costs nothing, and a real navigation joins the job in
 * flight rather than starting a second. */
const warmed = new Set();

function warmSite(domain) {
  if (!state.settings.prefetch || !domain || warmed.has(domain) || !domain.includes('.')) return;
  warmed.add(domain);
  fetch('/api/prefetch/site?domain=' + encodeURIComponent(domain), { method: 'POST' }).catch(() => {});
}

function domainOf(text) {
  const raw = String(text || '').trim().toLowerCase();
  if (!raw || /\s/.test(raw)) return '';                       // a search, not an address
  const host = raw.replace(/^https?:\/\//, '').split(/[/?#]/)[0];
  return /^[a-z0-9.-]+\.[a-z]{2,}$/.test(host) ? host : '';
}

function warmSiteFor(text) {
  clearTimeout(state.typeTimer);
  const domain = domainOf(text);
  if (!domain) return;
  state.typeTimer = setTimeout(() => warmSite(domain), 400);   // once they stop typing
}

/* A results page is a list of domains about to be clicked. Warm the first few
 * profiles while the reader is still reading, so the click lands on a page that
 * can start writing immediately. */
function warmResultSites(entry) {
  try {
    const doc = entry.frame && entry.frame.contentDocument;
    if (!doc) return;
    const seen = [];
    doc.querySelectorAll('a.title[href]').forEach((link) => {
      const domain = domainOf(link.getAttribute('href'));
      if (domain && !seen.includes(domain)) seen.push(domain);
    });
    seen.slice(0, 3).forEach(warmSite);
  } catch (e) { /* the frame went away mid-thought */ }
}

/* Hovering a link is a guess at the next page, and the two things worth guessing
 * cost wildly different amounts -- so they wait different lengths of time.
 *
 * The site profile is ~220 tokens and is wanted by every page on the domain, so
 * a glance is evidence enough: even if the reader never clicks, the next visitor
 * to that domain gets it free. A whole page is ~2,300 tokens and takes the model
 * off everything else for the duration, which is the entire cost of a wrong
 * guess -- the reader who then clicks somewhere else waits for a page nobody
 * will ever read. That one wants to be sure, so it waits for a rest rather than
 * a pass. 220ms used to buy both, which meant sweeping the pointer across a
 * paragraph of links committed the model to whichever one it crossed first. */
const HOVER_SITE_MS = 90;
const HOVER_PAGE_MS = 650;

function queuePrefetch(tab, url, text) {
  if (!state.settings.prefetch || !url || !url.startsWith('http')) return;
  if (url === state.hoverUrl) return;
  clearTimeout(state.hoverTimer);
  clearTimeout(state.hoverSiteTimer);

  state.hoverSiteTimer = setTimeout(() => warmSite(domainOf(url)), HOVER_SITE_MS);
  state.hoverTimer = setTimeout(() => {
    state.hoverUrl = url;
    const params = new URLSearchParams({ q: url, text: text || '' });
    if (tab.url.startsWith('http')) params.set('from', tab.url);
    fetch('/api/prefetch?' + params, { method: 'POST' }).catch(() => {});
  }, HOVER_PAGE_MS);
}

/* -------------------------------------------------------------- favourites */

async function loadBookmarks() {
  try {
    const data = await fetch('/api/bookmarks').then((r) => r.json());
    state.bookmarks = data.bookmarks || [];
  } catch (e) {
    state.bookmarks = [];
  }
  renderBookmarks();
}

function isStarred(url) {
  return state.bookmarks.some((b) => b.url === url);
}

function syncStar() {
  const tab = active();
  const on = !!(tab && tab.url.startsWith('http') && isStarred(tab.url));
  const star = $('star');
  star.textContent = on ? '★' : '☆';
  star.classList.toggle('on', on);
  star.title = on ? 'In your favourites — click to remove' : 'Add to favourites (kept when you forget every page)';
  star.disabled = !(tab && tab.url.startsWith('http'));
}

async function toggleStar() {
  const tab = active();
  if (!tab || !tab.url.startsWith('http')) return;
  const on = isStarred(tab.url);
  try {
    const res = on
      ? await fetch('/api/bookmarks?url=' + encodeURIComponent(tab.url), { method: 'DELETE' })
      : await fetch('/api/bookmarks', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ url: tab.url, title: tab.title }),
        });
    const data = await res.json();
    if (data.bookmarks) state.bookmarks = data.bookmarks;
  } catch (e) { /* leave the star as it was */ }
  renderBookmarks();
  syncStar();
}

function renderBookmarks() {
  const bar = $('bookmarks');
  bar.innerHTML = '';
  bar.hidden = !state.bookmarks.length;

  state.bookmarks.forEach((mark) => {
    const el = document.createElement('span');
    el.className = 'fav';

    const link = document.createElement('button');
    link.className = 'fav-open';
    link.textContent = mark.title || mark.domain || mark.url;
    link.title = mark.url;
    link.onclick = () => navigate(active(), mark.url);
    el.appendChild(link);

    const x = document.createElement('button');
    x.className = 'fav-x';
    x.textContent = '×';
    x.title = 'Remove from favourites';
    x.onclick = async (e) => {
      e.stopPropagation();
      const data = await fetch('/api/bookmarks?url=' + encodeURIComponent(mark.url), { method: 'DELETE' })
        .then((r) => r.json()).catch(() => null);
      if (data && data.bookmarks) state.bookmarks = data.bookmarks;
      renderBookmarks();
      syncStar();
    };
    el.appendChild(x);

    bar.appendChild(el);
  });
}

/* -------------------------------------------------------------- shortcuts */

function shortcut(k) {
  const tab = active();
  const alt = k.alt;
  if (alt && k.key === 't') { createTab(); return true; }
  if (alt && k.key === 'w') { closeTab(state.activeId); return true; }
  if (alt && (k.key === 'd' || k.key === 'l')) { $('address').focus(); $('address').select(); return true; }
  if (alt && k.key === 'r' && tab) { navigate(tab, tab.url, { push: false }); return true; }
  if (alt && k.key === 's') { toggleStar(); return true; }
  if (alt && k.key === 'ArrowLeft') { go(-1); return true; }
  if (alt && k.key === 'ArrowRight') { go(1); return true; }
  return false;
}

/* ------------------------------------------------------------------ wiring */

function wire() {
  $('newTab').onclick = () => createTab();
  $('back').onclick = () => go(-1);
  $('forward').onclick = () => go(1);
  $('home').onclick = () => showNewTab(active());
  $('star').onclick = toggleStar;
  $('reload').onclick = () => {
    const tab = active();
    if (!tab) return;
    if (tab.loading) { stop(tab); setStatus('Stopped'); syncChrome(); }
    else if (tab.url === NEW_TAB) showNewTab(tab, false);
    else navigate(tab, tab.url, { push: false });
  };
  $('regen').onclick = () => {
    const tab = active();
    if (tab && tab.url !== NEW_TAB) navigate(tab, tab.url, { push: false, fresh: true });
  };

  $('address').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const tab = active();
    const value = $('address').value.trim();
    if (!tab || !value) return;
    navigate(tab, value, { from: tab.url.startsWith('http') ? tab.url : '' });
    $('address').blur();
  });
  $('address').addEventListener('focus', () => $('address').select());
  // Typing a domain is the earliest possible warning that its profile will be
  // wanted, and the profile is the one call that blocks a page on a new site.
  $('address').addEventListener('input', () => warmSiteFor($('address').value));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { const tab = active(); if (tab && tab.loading) { stop(tab); syncChrome(); } return; }
    if (!(e.altKey || e.ctrlKey || e.metaKey)) return;
    if (shortcut({ key: e.key, alt: e.altKey, ctrl: e.ctrlKey, meta: e.metaKey })) e.preventDefault();
  });

  $('historyBtn').onclick = openHistory;
  $('closeHistory').onclick = () => { $('historyPanel').hidden = true; };
  $('clearHistory').onclick = async () => { await fetch('/api/history', { method: 'DELETE' }); openHistory(); };

  $('settingsBtn').onclick = openSettings;
  $('closeSettings').onclick = () => { $('settingsModal').hidden = true; };
  $('saveSettings').onclick = saveSettings;
  $('refreshModels').onclick = loadModels;
  $('clearCache').onclick = async () => {
    const res = await fetch('/api/cache', { method: 'DELETE' }).then((r) => r.json());
    warmed.clear();  // those domains have no profile any more, so they are worth warming again
    $('settingsNote').textContent =
      `Forgot ${res.pages} pages${res.kept ? `, kept ${res.kept} belonging to favourites` : ''}. `
      + 'Those URLs will be imagined afresh — as different places.';
  };

  $('closeWizard').onclick = () => { $('wizard').hidden = true; };
  $('health').onclick = () => checkSetup(true);
}

/* ----------------------------------------------------------------- history */

async function openHistory() {
  $('historyPanel').hidden = false;
  const list = $('historyList');
  list.innerHTML = '<div class="empty">Reading…</div>';
  const data = await fetch('/api/history').then((r) => r.json()).catch(() => ({ history: [] }));
  const items = data.history || [];
  if (!items.length) return void (list.innerHTML = '<div class="empty">Nothing yet.</div>');

  list.innerHTML = '';
  items.forEach((entry) => {
    const button = document.createElement('button');
    button.className = 'hentry';
    button.innerHTML = `<b>${esc(entry.title)}</b><small>${esc(entry.url)}</small>`;
    button.onclick = () => { navigate(active(), entry.url); $('historyPanel').hidden = true; };
    list.appendChild(button);
  });
}

/* ---------------------------------------------------------------- settings */

function openSettings() {
  const s = state.settings;
  $('setUrl').value = s.ollamaUrl || '';
  $('setModel').value = s.model || '';
  $('setTemp').value = s.temperature;
  $('setPredict').value = s.numPredict;
  $('setCtx').value = s.numCtx;
  $('setGpu').value = s.numGpu;
  $('setBatch').value = s.numBatch;
  $('setKeep').value = s.keepAlive || '30m';
  $('setThink').checked = !!s.think;
  $('setCache').checked = !!s.useCache;
  $('setPrefetch').checked = !!s.prefetch;

  fill($('setStyle'), state.meta.styles, s.style);
  fill($('setEffort'), state.meta.efforts, s.effort);

  $('settingsNote').textContent = state.meta.mock ? 'Running the mock provider (OB_MOCK=1) — no model is involved.' : '';
  $('settingsModal').hidden = false;
  loadModels();
}

function fill(select, options, chosen) {
  select.innerHTML = '';
  (options || []).forEach((option) => {
    const el = document.createElement('option');
    el.value = option.key;
    el.textContent = option.note ? option.label + ' — ' + option.note : option.label;
    if (option.key === chosen) el.selected = true;
    select.appendChild(el);
  });
}

async function loadModels() {
  const note = $('modelNote');
  note.textContent = 'Asking Ollama what it has…';
  const data = await fetch('/api/models').then((r) => r.json()).catch(() => ({ models: [] }));
  const list = $('modelList');
  list.innerHTML = '';
  (data.models || []).forEach((model) => {
    const option = document.createElement('option');
    option.value = model.name;
    option.textContent = `${model.name} · ${model.parameters || ''} ${model.quantization || ''}`.trim();
    list.appendChild(option);
  });
  note.textContent = data.models && data.models.length
    ? `${data.models.length} model(s) installed: ${data.models.map((m) => m.name).join(', ')}`
    : (data.message || 'No models found. Open the setup wizard to pull one.');
}

async function saveSettings() {
  const patch = {
    ollamaUrl: $('setUrl').value,
    model: $('setModel').value,
    temperature: Number($('setTemp').value),
    numPredict: Number($('setPredict').value),
    numCtx: Number($('setCtx').value),
    numGpu: Number($('setGpu').value),
    numBatch: Number($('setBatch').value),
    keepAlive: $('setKeep').value,
    style: $('setStyle').value,
    effort: $('setEffort').value,
    think: $('setThink').checked,
    useCache: $('setCache').checked,
    prefetch: $('setPrefetch').checked,
  };
  const data = await fetch('/api/settings', {
    method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(patch),
  }).then((r) => r.json());

  state.settings = data.settings || state.settings;
  $('settingsModal').hidden = true;
  setStatus('Settings saved. Warming the model…');
  fetch('/api/warmup', { method: 'POST' }).then(() => setStatus('Model ready')).catch(() => {});
  refreshHealth();
}

/* ------------------------------------------------------------------ health */

async function refreshHealth() {
  const dot = $('health');
  try {
    const data = await fetch('/api/health').then((r) => r.json());
    state.health = data;
    dot.className = 'health ' + (data.ok ? 'ok' : 'bad');
    const gpu = data.gpu && data.gpu.loaded ? ` · loaded, ${data.gpu.percent}% on GPU` : (data.ok ? ' · idle' : '');
    dot.title = data.ok
      ? `${data.model} on ${data.endpoint}${gpu}`
      : `${data.message || 'Ollama unreachable'} — click to set it up`;
  } catch (err) {
    dot.className = 'health bad';
    dot.title = 'The browser server is not answering.';
  }
}

/* ------------------------------------------------------------------ wizard */

async function checkSetup(force) {
  if (state.meta.mock && !force) return;
  try {
    state.setup = await fetch('/api/setup/status').then((r) => r.json());
  } catch (err) {
    return;
  }
  renderWizard();
  if (force || !state.setup.ready) $('wizard').hidden = false;
}

function renderWizard() {
  const s = state.setup;
  if (!s) return;

  const steps = [
    {
      done: s.installed,
      title: 'Ollama installed',
      note: s.installed
        ? `version ${s.version || 'unknown'} · ${s.binary}`
        : (s.latestVersion ? `not found on PATH — latest release is ${s.latestVersion}` : 'not found on PATH'),
    },
    { done: s.serverUp, title: 'Ollama running', note: s.serverUp ? s.endpoint : 'not answering' },
    {
      done: !!(s.qwen && s.qwen.length),
      title: 'A model to browse with',
      note: (s.models && s.models.length) ? s.models.join(', ') : 'nothing pulled yet',
    },
  ];

  /* Only worth a line once there is a model loaded to say something about --
   * and then it is the most important line here, because a model that is only
   * half on the GPU is slower than every other setting in this program combined. */
  const tuning = s.tuning || {};
  if (s.gpu && s.gpu.loaded) {
    const whole = s.gpu.percent >= 100;
    steps.push({
      done: whole,
      title: whole ? 'Model fully on the GPU' : 'Model only partly on the GPU',
      note: whole
        ? `${s.gpu.percent}% in video memory`
        : `${s.gpu.percent}% in video memory — the rest runs on the CPU, which is where the slowness is`,
    });
  }

  $('wizSteps').innerHTML = steps.map((step) =>
    `<div class="step ${step.done ? 'done' : ''}"><span class="mark">${step.done ? '✓' : '○'}</span>
     <div><b>${esc(step.title)}</b><small>${esc(step.note)}</small></div></div>`).join('');

  const gpu = s.recommend.vramGb ? `${s.recommend.vramGb} GB of video memory detected` : 'no GPU detected — expect CPU speed';
  const actions = $('wizActions');
  actions.innerHTML = '';

  if (!s.installed) {
    const version = s.latestVersion ? ' ' + s.latestVersion : '';
    if (s.canInstall) {
      const pin = document.createElement('input');
      pin.id = 'wizVersion';
      pin.className = 'wiz-pin';
      pin.placeholder = s.latestVersion ? `${s.latestVersion} (latest)` : 'latest';
      pin.spellcheck = false;
      pin.title = 'Leave empty for the latest release, or pin a version like 0.5.7';

      actions.appendChild(button('Install Ollama' + version, 'primary', () => {
        const wanted = ($('wizVersion').value || '').trim();
        runSetup('/api/setup/install' + (wanted ? '?version=' + encodeURIComponent(wanted) : ''));
      }));
      actions.appendChild(pin);
      actions.insertAdjacentHTML('beforeend', `<div class="wiz-note">Runs the official installer as administrator:
        <code>${esc(s.installCommand)}</code>The version it lands is printed below once it's done.</div>`);
    } else {
      actions.innerHTML = `<div class="wiz-note">Installing needs admin rights this page can't ask for. Run this in a
        terminal, then come back:<code>${esc(s.installCommand)}</code>
        ${s.latestVersion ? `That installs ollama ${esc(s.latestVersion)}, the latest release.` : ''}</div>`;
    }
  } else if (!s.serverUp) {
    actions.appendChild(button('Start Ollama', 'primary', () => runSetup('/api/setup/serve')));
  } else if (!s.models.length || !s.configuredPresent) {
    const select = document.createElement('select');
    select.id = 'wizModel';
    (s.tiers || []).forEach((tier) => {
      const option = document.createElement('option');
      option.value = tier.model;
      option.textContent = `${tier.model} · ${tier.download} · ${tier.note}`;
      if (tier.model === s.recommend.model) option.selected = true;
      select.appendChild(option);
    });
    actions.appendChild(select);
    actions.appendChild(button(`Download ${s.recommend.model}`, 'primary', () => {
      runSetup('/api/setup/pull?model=' + encodeURIComponent($('wizModel').value));
    }));
    actions.insertAdjacentHTML('beforeend', `<div class="wiz-note">${esc(gpu)}. Downloads once, then stays put.</div>`);
  } else {
    actions.innerHTML = `<div class="wiz-note">Ready: <b>${esc(s.configuredModel)}</b> on ${esc(s.endpoint)}. ${esc(gpu)}.</div>`;

    /* The one remaining thing worth pressing. Offered last so it never stands
     * between anyone and a working browser, and only while it would change
     * something -- once both variables are set there is nothing to say. */
    if (tuning.supported && !tuning.applied) {
      const squeezed = s.gpu && s.gpu.loaded && s.gpu.percent < 100;
      if (tuning.canApply) {
        actions.appendChild(button('Free up video memory', squeezed ? 'primary' : '', () => runSetup('/api/setup/tune')));
      }
      actions.insertAdjacentHTML('beforeend', `<div class="wiz-note">
        Flash attention and an 8-bit KV cache on the Ollama daemon give back ${esc(tuning.saves)} —
        ${squeezed
          ? 'which is what is currently keeping part of the model off the GPU.'
          : 'headroom for a larger context or a larger model.'}
        ${tuning.canApply
          ? 'Restarts Ollama, which takes a few seconds.'
          : `Needs admin rights this page can't ask for — run this instead:<code>${esc(tuning.command)}</code>`}
      </div>`);
    }

    actions.appendChild(button('Start browsing', 'primary', () => { $('wizard').hidden = true; }));
  }
}

function button(label, kind, onclick) {
  const el = document.createElement('button');
  el.className = 'pill ' + (kind || '');
  el.textContent = label;
  el.onclick = onclick;
  return el;
}

async function runSetup(url) {
  const log = $('wizLog');
  const wrap = $('wizProgressWrap');
  log.hidden = false;
  log.textContent = '';
  $('wizActions').querySelectorAll('button').forEach((b) => { b.disabled = true; });

  const append = (line) => { log.textContent += line + '\n'; log.scrollTop = log.scrollHeight; };

  try {
    const res = await fetch(url, { method: 'POST' });
    await readEvents(res.body, (type, data) => {
      if (type === 'log') append(data.text);
      else if (type === 'progress') {
        wrap.hidden = false;
        const pct = data.percent == null ? null : data.percent;
        $('wizProgress').style.width = (pct == null ? 4 : pct) + '%';
        const size = data.total ? ` ${(data.completed / 1e9).toFixed(2)} / ${(data.total / 1e9).toFixed(2)} GB` : '';
        log.textContent = log.textContent.replace(/\n?[^\n]*$/, '\n' + data.status + size + (pct == null ? '' : ` (${pct}%)`));
      } else if (type === 'error') {
        append('✗ ' + data.message + (data.hint ? '\n  ' + data.hint : ''));
      } else if (type === 'exit') {
        append(data.code === 0 ? '✓ done' : `✗ exited with ${data.code}`);
      }
    });
  } catch (err) {
    append('✗ ' + err);
  }

  wrap.hidden = true;
  await checkSetup(true);
  await refreshHealth();
}

boot();
