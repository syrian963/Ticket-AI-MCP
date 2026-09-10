"""Speak MCP to the installed server over stdio and count the tools.

The check above proves the entry point imports. A server that imports and
then answers nothing is the failure a client actually meets, and it is the
only failure that matters for a thing whose entire job is to be spoken to.

Plain JSON-RPC over a pipe, no client library: the point is to be a stranger.
"""

import json
import subprocess
import sys

binary = sys.argv[1]


def frame(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode()


proc = subprocess.Popen(
    [binary],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TICKET_AI_CACHE_DIR": "/tmp/stdio-cache"},
)

send = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "install-check", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]

out, _ = proc.communicate(b"".join(frame(m) for m in send), timeout=60)

tools: list[str] = []
for line in out.decode(errors="replace").splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if message.get("id") == 2:
        tools = [t["name"] for t in message.get("result", {}).get("tools", [])]

if not tools:
    print("no tools/list answer over stdio", file=sys.stderr)
    print(out.decode(errors="replace")[:400], file=sys.stderr)
    raise SystemExit(1)

expected = {
    "learn_conventions",
    "house_style",
    "ticket_template",
    "ticket_context",
    "template_gaps",
    "review_draft",
    "review_ticket",
    "review_open_tickets",
}
missing = expected - set(tools)
if missing:
    print(f"the server did not offer: {sorted(missing)}", file=sys.stderr)
    raise SystemExit(1)
print(f"{len(tools)} tools over stdio")
