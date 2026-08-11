/* Runs inside every generated page.
 *
 * There is no network here. This turns the document into a citizen of the
 * imagined web: links and forms become navigation requests to the chrome,
 * <img> without a src gets painted from its alt text, and anything that tries
 * to reach outside is turned off. (A CSP on the document says the same thing;
 * this is the half that can hand the click back to the browser.)
 */
(function () {
  var CFG = window.__HLG__ || {};
  var PAGE = CFG.url || 'https://example.com/';
  var ORIGIN = CFG.origin || '';

  function post(message) {
    try {
      message.__ob = true;
      parent.postMessage(message, '*');
    } catch (e) { /* nothing we can do from in here */ }
  }

  function resolve(href) {
    if (!href) return null;
    var raw = String(href).trim();
    if (!raw || raw.charAt(0) === '#') return null;
    if (/^(javascript|mailto|tel|data|blob|file):/i.test(raw)) return null;
    if (/^hlg:/i.test(raw)) return raw;
    try {
      return new URL(raw, PAGE).href;
    } catch (e) {
      return null;
    }
  }

  function follow(url, opts) {
    if (!url) return;
    if (/^hlg:/i.test(url)) {
      post({ type: 'action', action: url.replace(/^hlg:/i, '') });
      return;
    }
    post({ type: 'navigate', url: url, newTab: !!(opts && opts.newTab), text: (opts && opts.text) || '' });
  }

  /* ------------------------------------------------------------- navigation */

  function anchorFrom(node) {
    while (node && node !== document) {
      if (node.tagName === 'A' && node.getAttribute('href') !== null) return node;
      node = node.parentNode;
    }
    return null;
  }

  document.addEventListener('click', function (e) {
    var a = anchorFrom(e.target);
    if (!a) return;
    e.preventDefault();
    follow(resolve(a.getAttribute('href')), {
      newTab: e.ctrlKey || e.metaKey || a.getAttribute('target') === '_blank',
      text: (a.textContent || '').trim().slice(0, 120)
    });
  }, true);

  document.addEventListener('auxclick', function (e) {
    if (e.button !== 1) return;
    var a = anchorFrom(e.target);
    if (!a) return;
    e.preventDefault();
    follow(resolve(a.getAttribute('href')), { newTab: true, text: (a.textContent || '').trim().slice(0, 120) });
  }, true);

  // A pointer resting on a link is the earliest hint of where you're going next,
  // and a local model needs every second of head start it can get.
  document.addEventListener('mouseover', function (e) {
    var a = anchorFrom(e.target);
    if (!a) return;
    var url = resolve(a.getAttribute('href'));
    if (url && !/^hlg:/i.test(url)) {
      post({ type: 'hover', url: url, text: (a.textContent || '').trim().slice(0, 120) });
    }
  }, true);

  document.addEventListener('mouseout', function (e) {
    if (anchorFrom(e.target)) post({ type: 'unhover' });
  }, true);

  // Search boxes are the point of a browser. Every form is a GET into the fake web.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || form.tagName !== 'FORM') return;
    e.preventDefault();

    var target = resolve(form.getAttribute('action')) || PAGE;
    var url;
    try {
      url = new URL(target);
    } catch (err) {
      return;
    }

    var fields = form.querySelectorAll('input, select, textarea');
    var used = 0;
    for (var i = 0; i < fields.length; i++) {
      var field = fields[i];
      var type = (field.type || '').toLowerCase();
      if (!field.value || type === 'submit' || type === 'button' || type === 'file' || type === 'password') continue;
      if ((type === 'checkbox' || type === 'radio') && !field.checked) continue;
      url.searchParams.set(field.name || (used === 0 ? 'q' : 'field' + i), field.value);
      used++;
    }
    follow(url.href, { text: 'search' });
  }, true);

  /* ----------------------------------------------------------------- images */

  function paint(img) {
    if (img.__ob) return;
    img.__ob = 1;

    var src = img.getAttribute('src') || '';
    if (src.indexOf(ORIGIN + '/api/img') === 0) return;

    var alt = img.getAttribute('alt') || '';
    if (!alt && src) {
      alt = decodeURIComponent(src.split('?')[0].split('/').pop() || '')
        .replace(/\.[a-z0-9]+$/i, '')
        .replace(/[-_+]+/g, ' ');
    }

    var w = parseInt(img.getAttribute('width'), 10) || 0;
    var h = parseInt(img.getAttribute('height'), 10) || 0;
    if (!w) {
      var box = img.parentElement ? img.parentElement.clientWidth : 0;
      w = Math.max(240, Math.min(1200, box || 800));
    }
    if (!h) h = Math.round(w * 0.5625);

    img.removeAttribute('srcset');
    img.src = ORIGIN + '/api/img?w=' + w + '&h=' + h + '&alt=' + encodeURIComponent(alt.slice(0, 180));
  }

  /* ----------------------------------------------------------------- sound */
  //
  // The same bargain as images: the model names a sound, and it gets built here
  // rather than fetched. `new Audio('/sounds/coin.wav')` is a coin -- a short
  // bright arpeggio -- because the filename says coin, and the same filename is
  // always the same sound.
  //
  // Oscillators, not files. WebAudio generates samples inside the page, so it
  // never asks the network for anything and the document's own `default-src
  // 'none'` has nothing to block. A <audio src> would have been refused; this
  // is the half of the illusion that can actually make a noise.

  var AC = null;
  function audio() {
    var Ctor = window.AudioContext || window.webkitAudioContext;
    if (!AC && Ctor) { try { AC = new Ctor(); } catch (e) { return null; } }
    // Autoplay policy: a context created before the first gesture starts
    // suspended, and a game that beeps on load would stay silent forever.
    if (AC && AC.state === 'suspended') { try { AC.resume(); } catch (e) {} }
    return AC;
  }

  function soundName(src) {
    return decodeURIComponent(String(src || '').split('?')[0].split('/').pop() || '')
      .replace(/\.[a-z0-9]+$/i, '')
      .replace(/[-_+]+/g, ' ')
      .toLowerCase();
  }

  function hash(text) {
    var h = 0;
    for (var i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) >>> 0;
    return h;
  }

  // [match, waveform, semitones from the root, seconds per step, decay]
  // Rising intervals read as reward, falling as loss -- which is most of what a
  // game sound has to say.
  var VOICES = [
    [/coin|ring|pickup|collect|point|score|gem|star/, 'square', [0, 7, 12], 0.05, 0.11],
    [/jump|hop|boing|spring|launch|fly/, 'sine', [0, 12], 0.07, 0.15],
    [/level|win|complete|power|bonus|unlock|success/, 'square', [0, 4, 7, 12], 0.07, 0.17],
    [/hit|hurt|damage|crash|explo|bump|break/, 'sawtooth', [0, -5, -12], 0.06, 0.17],
    [/over|lose|lost|fail|die|death|defeat/, 'triangle', [0, -3, -7, -12], 0.11, 0.28],
    [/click|tick|beep|blip|select|menu|type|key/, 'square', [0], 0.03, 0.04],
  ];

  function voiceFor(name) {
    for (var i = 0; i < VOICES.length; i++) if (VOICES[i][0].test(name)) return VOICES[i];
    return [null, 'sine', [0, 5], 0.06, 0.12];  // unnamed: still a sound, just a plain one
  }

  function beep(name) {
    var ac = audio();
    if (!ac) return;
    var voice = voiceFor(name);
    var wave = voice[1], steps = voice[2], gap = voice[3], decay = voice[4];
    // Pitch from the name, so two different coins are two different coins --
    // and the same one never drifts. Kept inside an octave of A4.
    var root = 220 * Math.pow(2, (hash(name) % 12) / 12);
    var start = ac.currentTime;

    for (var i = 0; i < steps.length; i++) {
      var at = start + i * gap;
      var osc = ac.createOscillator();
      var gain = ac.createGain();
      osc.type = wave;
      osc.frequency.setValueAtTime(root * Math.pow(2, steps[i] / 12), at);
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.18, at + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + decay);
      osc.connect(gain);
      gain.connect(ac.destination);
      osc.start(at);
      osc.stop(at + decay + 0.02);
    }
  }

  // Keep the URL, drop the fetch: the element stays a real media element so a
  // game can read .currentTime or .loop without throwing, but nothing loads.
  function silence(el) {
    var src = el.getAttribute('src') || el.src || '';
    if (src) {
      el.setAttribute('data-ob-sound', src);
      el.removeAttribute('src');
      try { el.src = ''; } catch (e) {}
    }
  }

  try {
    var NativeAudio = window.Audio;
    window.Audio = function (src) {
      var el = document.createElement('audio');
      if (src) el.setAttribute('data-ob-sound', src);
      return el;
    };
    window.Audio.prototype = NativeAudio ? NativeAudio.prototype : {};
  } catch (e) {}

  try {
    var Media = window.HTMLMediaElement;
    if (Media && Media.prototype) {
      Media.prototype.play = function () {
        beep(soundName(this.getAttribute('data-ob-sound') || this.getAttribute('src') || ''));
        return Promise.resolve();
      };
      Media.prototype.pause = function () {};
      Media.prototype.load = function () {};
    }
  } catch (e) {}

  // Nothing can be embedded from outside, so give those boxes something to be.
  function defuse(el) {
    if (el.__ob) return;
    el.__ob = 1;
    var box = document.createElement('div');
    box.textContent = '▶ embedded media';
    box.setAttribute(
      'style',
      'display:flex;align-items:center;justify-content:center;min-height:170px;' +
      'background:rgba(128,128,128,.14);border:1px dashed rgba(128,128,128,.4);border-radius:8px;' +
      'font:13px system-ui,sans-serif;opacity:.65'
    );
    if (el.parentNode) el.parentNode.replaceChild(box, el);
  }

  function sweep() {
    var imgs = document.getElementsByTagName('img');
    for (var i = 0; i < imgs.length; i++) paint(imgs[i]);

    var frames = document.querySelectorAll('iframe, object, embed');
    for (var j = 0; j < frames.length; j++) defuse(frames[j]);

    var sources = document.getElementsByTagName('source');
    while (sources.length) sources[0].parentNode.removeChild(sources[0]);

    // <audio src> written into the page directly, rather than through Audio().
    var media = document.querySelectorAll('audio, video');
    for (var k = 0; k < media.length; k++) silence(media[k]);

    if (document.title && document.title !== window.__obTitle) {
      window.__obTitle = document.title;
      post({ type: 'title', title: document.title });
    }
  }

  window.__obSweep = sweep;

  if (window.MutationObserver) {
    new MutationObserver(sweep).observe(document.documentElement || document, { childList: true, subtree: true });
  }
  document.addEventListener('DOMContentLoaded', sweep);
  window.addEventListener('load', sweep);
  setTimeout(sweep, 60);

  /* ------------------------------------------------------------ no outside */

  try { window.fetch = function () { return Promise.reject(new TypeError('offline')); }; } catch (e) {}
  try {
    window.XMLHttpRequest = function () {
      this.open = function () {};
      this.send = function () { if (typeof this.onerror === 'function') this.onerror(new Error('offline')); };
      this.setRequestHeader = function () {};
      this.abort = function () {};
      this.addEventListener = function () {};
    };
  } catch (e) {}
  try { window.WebSocket = function () { throw new Error('offline'); }; } catch (e) {}
  try { window.EventSource = function () { throw new Error('offline'); }; } catch (e) {}
  try {
    window.open = function (href) { follow(resolve(href), { newTab: true }); return null; };
  } catch (e) {}

  /* -------------------------------------------------- chrome keyboard keys */
  // The page has focus while you play, so browser shortcuts have to be relayed.

  var RELAY = { t: 1, w: 1, d: 1, r: 1, l: 1, ArrowLeft: 1, ArrowRight: 1 };
  document.addEventListener('keydown', function (e) {
    if (!(e.altKey || e.ctrlKey || e.metaKey)) return;
    if (!RELAY[e.key]) return;
    if (e.altKey) e.preventDefault();
    post({ type: 'shortcut', key: e.key, alt: e.altKey, ctrl: e.ctrlKey, meta: e.metaKey, shift: e.shiftKey });
  }, true);

  post({ type: 'ready', url: PAGE });
})();
