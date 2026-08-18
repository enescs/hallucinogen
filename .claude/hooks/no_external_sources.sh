#!/bin/sh
# Starts the no-lookup guard with whatever this machine calls python.
#
# The hook command in settings.json is one string shared by every contributor,
# and there is no spelling of the interpreter that exists everywhere: an Ubuntu
# without python-is-python3 has only `python3`, a Windows from python.org has
# only `python` and `py`. That matters more here than in a build script, because
# a hook that cannot start is a guard that fails open -- the tool it was meant to
# deny runs anyway, behind an error nobody has to read. So the choice is made
# here, in the one shell present wherever the hook already works at all.
#
# `exec`, rather than a `python3 ... || python ...` chain in settings.json: the
# guard answers on stdout with a JSON decision and exits 0 whether it allowed or
# denied, so what the hook needs from this file is a transparent launcher and not
# a layer with opinions of its own. exec replaces the shell, which leaves stdin,
# stdout and the exit status the guard's own, and runs it exactly once.
set -u

HOOK="$(dirname "$0")/no_external_sources.py"

for py in python3 python py; do
    if command -v "$py" >/dev/null 2>&1; then
        exec "$py" "$HOOK" "$@"
    fi
done

# No interpreter, so no decision to report: say so on stderr and exit nonzero,
# which Claude Code shows without blocking the call. Denying every lookup would
# be a strange way to announce a missing python in a project written in it.
echo "no-lookup guard did not run: no python3, python or py on PATH" >&2
exit 1
