"""Host-side ops API for the NemoClaw sandbox agent: the same whitelisted tools as agent.py, over HTTP.

  uv run --no-sync scripts/ops_api.py            # listens on 0.0.0.0:8790 (env OPS_PORT), logs every call to logs/ops-api.log
  GET  /tools                                     # tool list with JSON schemas (give this to the agent)
  POST /call  {"name": "<tool>", "args": {...}}   # -> {"result": "..."}

Only the two sandbox containers and victim/conf.d are reachable through it (see agent.run_tool). No auth by design for
the lab drill; put it behind a token or a firewall rule before exposing beyond the docker bridge.
"""
import json
import os
import pathlib
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agent  # noqa: E402  (reuses TOOLS and run_tool)

LOG = pathlib.Path(__file__).resolve().parent.parent / "logs" / "ops-api.log"
LOG.parent.mkdir(exist_ok=True)
TOOLS = [t for t in agent.TOOLS if t["name"] != "finish"]


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/tools":
            return self._send(200, {"tools": TOOLS})
        self._send(404, {"error": "use GET /tools or POST /call"})

    def do_POST(self):
        if self.path.rstrip("/") != "/call":
            return self._send(404, {"error": "use POST /call"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or b"{}"))
            name, args = req["name"], req.get("args") or {}
        except Exception as e:
            return self._send(400, {"error": f"bad request: {e}"})
        if name not in {t["name"] for t in TOOLS}:
            return self._send(400, {"error": f"unknown tool {name}"})
        t0 = time.time()
        result = agent.run_tool(name, args, dry_run=os.environ.get("OPS_DRY_RUN") == "1")
        with LOG.open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), "from": self.client_address[0], "tool": name,
                                "args": args, "ms": int((time.time() - t0) * 1000), "result": result[:300]}, ensure_ascii=False) + "\n")
        self._send(200, {"result": result})

    def log_message(self, *_):  # quiet; the JSONL log is the record
        pass


if __name__ == "__main__":
    port = int(os.environ.get("OPS_PORT", "8790"))
    print(f"ops api on 0.0.0.0:{port}, tools: {[t['name'] for t in TOOLS]}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
