"""Tiny OpenAI-API shim in front of trtllm-serve for clients that send shapes TensorRT-LLM rejects.

  uv run --no-sync scripts/oai_shim.py      # listens 0.0.0.0:8001 (SHIM_PORT), forwards to UPSTREAM (default http://127.0.0.1:8000)

Normalizations (only on /v1/chat/completions): role "developer" -> "system"; array `content` of text parts -> one string;
drops fields TensorRT-LLM does not know (`store`, `metadata`). Everything else is passed through unchanged, streaming too.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8000").rstrip("/")
DROP = ("store", "metadata")


DEBUG = os.environ.get("SHIM_DEBUG") == "1"


def normalize(body: dict) -> dict:
    shapes = []
    for m in body.get("messages", []):
        if m.get("role") == "developer":
            m["role"] = "system"
        c = m.get("content")
        shapes.append(f"{m.get('role')}:{type(c).__name__}{'+tools' if m.get('tool_calls') else ''}")
        if isinstance(c, list):
            m["content"] = "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") in (None, "text"))
        elif c is None and m.get("role") != "assistant":
            m["content"] = ""          # TensorRT-LLM only allows null content on assistant messages
        if m.get("role") == "assistant":   # keep only the OpenAI-standard keys; TRT-LLM validates typed dicts strictly
            calls = [{"id": tc.get("id"), "type": "function",
                      "function": {"name": tc["function"]["name"],
                                   "arguments": tc["function"]["arguments"] if isinstance(tc["function"].get("arguments"), str)
                                   else json.dumps(tc["function"].get("arguments") or {})}}
                     for tc in (m.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("function")]
            keep = {"role": "assistant", "content": m.get("content")}
            if calls:
                keep["tool_calls"] = calls
            m.clear(); m.update(keep)
        elif m.get("role") == "tool":
            keep = {"role": "tool", "content": m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")),
                    "tool_call_id": m.get("tool_call_id")}
            if DEBUG:
                shapes[-1] += " => " + (keep["content"] or "")[:160].replace("\n", " ")
            m.clear(); m.update(keep)
    for k in DROP:
        body.pop(k, None)
    if DEBUG:
        with open(os.path.join(os.path.dirname(__file__), "..", "logs", "oai-shim.log"), "a") as f:
            f.write(json.dumps({"messages": shapes, "tools": len(body.get("tools") or []), "stream": body.get("stream"),
                                "keys": sorted(k for k in body if k not in ("messages", "tools"))}) + "\n")
    return body


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, data=None):
        req = urllib.request.Request(UPSTREAM + self.path, data=data, method=self.command,
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "transfer-encoding")})
        try:
            resp = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            resp = e
            if DEBUG and e.code >= 400:
                body = e.read(); resp = e
                with open(os.path.join(os.path.dirname(__file__), "..", "logs", "oai-shim.log"), "a") as f:
                    f.write(json.dumps({"upstream_error": e.code, "body": body[:900].decode(errors="replace")}) + "\n")
                self.send_response(e.code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        chunked = "text/event-stream" in (resp.headers.get("Content-Type") or "")
        payload = None if chunked else resp.read()
        if payload is not None:
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for line in resp:
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self):
        self._forward()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path.startswith("/v1/chat/completions"):
            try:
                raw = json.dumps(normalize(json.loads(raw))).encode()
            except Exception:
                pass
        self._forward(raw)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("SHIM_PORT", "8001"))
    print(f"oai shim on 0.0.0.0:{port} -> {UPSTREAM}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
