#!/usr/bin/env bash
# Install the built wheel into a throwaway environment and drive it.
#
# `uv run` in the source tree proves the source tree works. It says nothing
# about whether the wheel carries every module, whether the entry points
# resolve outside the project, or whether a data file was left behind - and
# those are exactly the failures a first release ships.
set -u
# The directory this script is in, not one developer's home. Hardcoding a path
# under $HOME meant the check could only ever pass on the machine it was
# written on - and the CI job that runs it has a checkout somewhere else
# entirely, so its first run would have failed on `cd`.
cd "$(dirname "$(readlink -f "$0")")" || exit 2
# Likewise for uv: on a runner it is on PATH and not under $HOME.
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
WHEEL=$(ls dist/*.whl 2>/dev/null | head -1)
[ -z "$WHEEL" ] && { echo "no wheel built"; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "installing $(basename "$WHEEL") into a clean venv"

"$UV" venv --quiet "$TMP/venv" || exit 2
VENV_PY="$TMP/venv/bin/python"
"$UV" pip install --quiet --python "$VENV_PY" "$WHEEL" || exit 2

fails=0
check() {
  printf '  %-46s ' "$1"; shift
  if out=$("$@" 2>&1); then echo "ok"; else echo "FAIL"; echo "$out" | tail -3 | sed 's/^/      /'; fails=$((fails+1)); fi
}

BIN="$TMP/venv/bin"

check "ticket-ai --help"            "$BIN/ticket-ai" --help
check "ticket-ai --version-ish"     "$BIN/ticket-ai" models --workflow
check "the mcp entry point imports" "$VENV_PY" -c "from ticket_ai_mcp.server import main"
check "every module imports"        "$VENV_PY" -c "
import importlib, pkgutil, ticket_ai_mcp
for m in pkgutil.walk_packages(ticket_ai_mcp.__path__, 'ticket_ai_mcp.'):
    importlib.import_module(m.name)
"
check "the ui page renders"         "$VENV_PY" -c "
from ticket_ai_mcp.ui import page
html = page('de')
assert 'Entwurf' in html and '<style>' in html
"
check "findings render in German"   "$VENV_PY" -c "
from ticket_ai_mcp.review import Finding
f = Finding(code='no_labels', severity='medium', params={'pct':'62%','suggestions':'Bug'})
what, why, fix = f.localised('de')
assert 'Labels' in what and '62%' in why, (what, why)
"
check "the tracker registry is full" "$VENV_PY" -c "
from ticket_ai_mcp.trackers import available
assert available() == ('github','gitlab','jira'), available()
"
# The one that matters. Everything above proves the wheel is complete; this
# proves the thing it installs will answer a client. A server that imports
# cleanly and then says nothing over stdio is the failure users actually meet,
# and nothing else here would notice it.
check "the server answers a client over stdio" "$VENV_PY" tools/stdio_probe.py "$BIN/ticket-ai-mcp"

check "an unconfigured run explains" sh -c "
out=\$($BIN/ticket-ai learn 2>&1); echo \"\$out\" | grep -q TICKET_AI_TRACKER
"

echo
if [ "$fails" -eq 0 ]; then echo "the wheel works standalone"; else echo "$fails check(s) failed"; fi
exit "$fails"
