"""Speak MCP to the installed server over stdio and count the tools.

The check above proves the entry point imports. A server that imports and
then answers nothing is the failure a client actually meets, and it is the
only failure that matters for a thing whose entire job is to be spoken to.

Plain JSON-RPC over a pipe, no client library: the point is to be a stranger.

**stdin stays open until the answer arrives.** `communicate` writes everything
and closes stdin in one go, and on a cold virtual environment the server lost
the race: it answered `initialize` and then reached end of input before it had
finished serving `tools/list`. Reproduced at three failures in six runs against
a freshly created venv, and zero in twelve against a warm one, which is why it
only ever went red on CI and never on a second local attempt. Reading until the
reply is in hand and closing stdin afterwards is zero for six.
"""

import json
import subprocess
import sys
import threading

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

assert proc.stdin and proc.stdout
proc.stdin.write(b"".join(frame(m) for m in send))
proc.stdin.flush()

tools: list[str] = []
seen: list[str] = []


def read_until_answer() -> None:
    """Read replies until the one to `tools/list` shows up, then stop."""
    global tools
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode(errors="replace").strip()
        seen.append(line)
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("id") == 2:
            tools = [t["name"] for t in message.get("result", {}).get("tools", [])]
            return


reader = threading.Thread(target=read_until_answer, daemon=True)
reader.start()
reader.join(60)

proc.stdin.close()
proc.kill()
proc.wait(timeout=10)

if not tools:
    print("no tools/list answer over stdio", file=sys.stderr)
    print(chr(10).join(seen)[:400], file=sys.stderr)
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
