# Security

## Reporting

Open a [private security advisory](https://github.com/enescs/hallucinogen/security/advisories/new)
rather than a public issue. If that isn't available to you, email
goksueneskerem@gmail.com.

This is a hobby project maintained by one person: there is no bounty, no SLA and
no guaranteed response time, and it will be looked at when it is looked at.
Please say what you did, what happened, and what you expected instead.

## What the threat model actually is

Hallucinogen runs on your machine, binds loopback, and talks to nothing but a
local Ollama. The interesting boundary is not the network — it is the one
between a generated page and the browser chrome around it, because a generated
page contains code a language model wrote and nothing vouches for it.

That boundary is held by two things, and both are in scope:

- A strict CSP on every generated document (`default-src 'none'`,
  `connect-src 'none'`), so a page cannot reach the network even if its script
  tries.
- `public/inject.js`, which replaces `fetch`, `XMLHttpRequest`, `WebSocket` and
  `window.open` before any page script runs, and intercepts links and form
  submits so a click inside an invented page can only produce another invented
  page.

**In scope:** anything that escapes that sandbox — a generated page reaching the
network, reading or writing outside its frame, touching the chrome's DOM or
storage, executing anything on the host, or escaping `data/` on disk. Also path
traversal in the page store, and command injection in the setup wizard, which
runs an installer with elevated rights.

**Not in scope:** the model writing something false, offensive or defamatory.
That is the entire premise of the project, not a vulnerability — see the README.
Likewise anything that requires you to have already set `HLG_ALLOW_PUBLIC=1`:
binding a public interface is documented as unsupported, and the program refuses
to do it without an explicit opt-in.

## If you expose it anyway

Don't. There is no authentication, no per-user isolation, no rate limiting and a
single shared page store, and none of that is an oversight to be reported — it
is what "single-user toy" means. `HLG_ALLOW_PUBLIC=1` exists so that doing it is
a decision rather than an accident, and it is your decision.
