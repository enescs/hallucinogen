# Contributing

## Licensing

Contributions are inbound=outbound: anything you submit is licensed under
[Apache 2.0](LICENSE), the same terms the project is released under. That is
Apache-2.0 §5's default and it is the whole of the arrangement — there is no CLA
to sign and no copyright to assign. You keep the copyright in what you wrote.

Only submit work you have the right to license this way. Don't paste in code
under a copyleft licence, code from an employer who owns it, or output you
haven't checked — and given what this project is, that last one is worth saying
plainly: an LLM will write you a plausible patch for a codebase it is guessing
at, and this repository has enough imagined software in it already.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
HLG_MOCK=1 .venv/bin/python run.py
```

```powershell
python -m venv .venv; .venv\Scripts\pip.exe install -r requirements.txt
$env:HLG_MOCK="1"; .venv\Scripts\python.exe run.py
```

Both platforms are supported and both are worth keeping working. Where they
genuinely differ — where the venv puts its interpreter, how a background process
is detached, whether a console can be written to in colour and in UTF-8, how a
command is spelled for a reader to paste — the branch belongs in
`server/portable.py` rather than as another `if os.name == "nt"` somewhere in
code that is otherwise about browsing.

Mock mode serves canned pages with no model involved, which is the fastest way
to work on the chrome, the stylesheets or the search page. Anything touching
prompts or generation needs a real model — `qwen3:4b` is quick enough to iterate
against.

## What tends to matter here

Most of this codebase is about latency, because a page is a few thousand tokens
and tokens-per-second is the whole experience. The Speed section of the README
is the design rationale, and it is worth reading before optimising something:
several of the obvious wins are already there, and a few of the obvious ideas
were tried and made things worse for reasons that are written down.

Two rules that are load-bearing rather than stylistic:

- **The model writes only `<main>`.** Everything around it is built by
  `theme.py`. If you find yourself asking the model for markup that could be
  built locally, build it locally — that was a recurring source of tokens
  generated and then discarded.
- **The shared prompt prefix is a cache, not a paragraph.** `_COMMON_SYSTEM` in
  `prompts.py` opens every prompt the module builds, and llama.cpp matches its
  cache from token zero. Putting anything variable in front of it invalidates
  ~1,100 tokens for every call that follows.

Comments here explain *why*, especially where the code looks odd because
something simpler was tried first. Match that. A diff that changes behaviour
without saying what it cost is hard to review against a file that says what
everything else cost.

## Scope

Bug fixes, new web eras, new `img.py` motifs, speed work and prompt improvements
are all welcome. Two things are out of scope and will be declined:

- **Hosted or multi-user modes.** Auth, accounts, per-user stores, a public
  deployment target. This is a single-user local toy on purpose, and `run.py`
  refuses a non-loopback bind for that reason.
- **Removing the model's own judgement.** The rule in `prompts.py` about never
  bailing out on a URL is scoped to unfamiliar URLs deliberately, and it stays
  that way. Patches that widen it back into a general "never refuse" are not a
  bug fix.
