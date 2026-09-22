"""Tiny OpenAI-API shim in front of trtllm-serve for clients that send shapes TensorRT-LLM rejects.

  uv run --no-sync scripts/oai_shim.py      # listens 0.0.0.0:8001 (SHIM_PORT), forwards to UPSTREAM (default http://127.0.0.1:8000)

Normalizations (only on /v1/chat/completions): role "developer" -> "system"; array `content` of text parts -> one string;
drops fields TensorRT-LLM does not know (`store`, `metadata`, `reasoning_effort`); enables the model's thinking mode via
`chat_template_kwargs` (SHIM_THINK=0 to disable), and recovers tool calls the model leaves inside its thinking
channel. Everything else is passed through unchanged, streaming too.
"""
import json
import os
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8000").rstrip("/")
DROP = ("store", "metadata", "reasoning_effort")   # reasoning_effort: TensorRT-LLM does not take it; we set thinking below
THINK = os.environ.get("SHIM_THINK", "1") == "1"   # Nemotron/Qwen thinking is a chat-template flag, not an API field


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
    if THINK and "messages" in body:               # same switch scripts/agent.py uses; OpenClaw cannot send it itself
        kw = body.setdefault("chat_template_kwargs", {})
        kw.setdefault("enable_thinking", True)   # the template's own default; set explicitly so it survives upgrades
    if DEBUG:
        log(messages=shapes, tools=len(body.get("tools") or []), stream=body.get("stream"),
            keys=sorted(k for k in body if k not in ("messages", "tools")))
    return body


TOOLCALL_RE = re.compile(r"<tool_call>\s*<function=([A-Za-z0-9_.\-]+)>(.*?)</function>\s*</tool_call>", re.S)
PARAM_RE = re.compile(r"<parameter=([A-Za-z0-9_.\-]+)>(.*?)</parameter>", re.S)


def log(**row):
    try:                                   # a log write must never break a request
        with open(os.path.join(os.path.dirname(__file__), "..", "logs", "oai-shim.log"), "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def salvage_calls(text, tools):
    """Recover tool calls the model wrote inside its thinking channel.

    Nemotron sometimes opens `<think>`, writes the `<tool_call>` XML block the chat template asks for, and stops
    without closing the tag. TensorRT-LLM's reasoning parser then files the whole thing as reasoning_content, so the
    tool parser never sees it and the turn reaches the client empty. The chat template anticipates this ("allow
    downstream logic to take care of broken thought"); this is that downstream logic.
    """
    types = {}
    for t in tools or []:
        fn = t.get("function") or t
        props = ((fn.get("parameters") or {}).get("properties") or {})
        types[fn.get("name")] = {k: (v or {}).get("type") for k, v in props.items() if isinstance(v, dict)}
    calls = []
    for name, inner in TOOLCALL_RE.findall(text or ""):
        args = {}
        for k, v in PARAM_RE.findall(inner):
            v, ty = v.strip(), types.get(name, {}).get(k)
            try:
                if ty == "integer":
                    v = int(v)
                elif ty == "number":
                    v = float(v)
                elif ty == "boolean":
                    v = v.strip().lower() in ("true", "1", "yes")
            except ValueError:
                pass
            args[k] = v
        calls.append({"id": f"call_salvaged_{len(calls)}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}})
    return calls


def salvage_stream(lines, tools):
    """Replay the upstream SSE lines untouched, unless the turn came back empty and holds a recoverable tool call."""
    content, reasoning, saw_calls = [], [], False
    ident = {"id": "chatcmpl-salvaged", "model": "edge-agent", "created": int(time.time())}
    for ln in lines:
        if not ln.startswith(b"data:"):
            continue
        chunk = ln[5:].strip()
        if not chunk or chunk == b"[DONE]":
            continue
        try:
            d = json.loads(chunk)
        except ValueError:
            continue
        for k in ident:
            if d.get(k):
                ident[k] = d[k]
        for ch in d.get("choices") or []:
            delta = ch.get("delta") or {}
            saw_calls = saw_calls or bool(delta.get("tool_calls"))
            content.append(delta.get("content") or "")
            reasoning.append(delta.get("reasoning_content") or "")
    if saw_calls or "".join(content).strip():
        return lines
    calls = salvage_calls("".join(reasoning), tools)
    if not calls:
        return lines
    log(salvaged=[c["function"]["name"] for c in calls], via="stream")
    head = dict(ident, object="chat.completion.chunk",
                choices=[{"index": 0, "delta": {"role": "assistant", "content": None,
                                                "tool_calls": [dict(c, index=i) for i, c in enumerate(calls)]},
                          "finish_reason": None}])
    tail = dict(ident, object="chat.completion.chunk",
                choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}])
    return [b"data: " + json.dumps(head).encode() + b"\n\n",
            b"data: " + json.dumps(tail).encode() + b"\n\n",
            b"data: [DONE]\n\n"]


def salvage_json(payload, tools):
    try:
        d = json.loads(payload)
    except ValueError:
        return payload
    for ch in d.get("choices") or []:
        m = ch.get("message") or {}
        if m.get("tool_calls") or (m.get("content") or "").strip():
            continue
        calls = salvage_calls(m.get("reasoning_content") or "", tools)
        if calls:
            m["tool_calls"], ch["finish_reason"] = calls, "tool_calls"
            log(salvaged=[c["function"]["name"] for c in calls], via="json")
            return json.dumps(d).encode()
    return payload


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, data=None, tools=None):
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
        payload = None if chunked else salvage_json(resp.read(), tools)
        if payload is not None:
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for line in salvage_stream(list(resp), tools):
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self):
        self._forward()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        tools = None
        if self.path.startswith("/v1/chat/completions"):
            try:
                body = normalize(json.loads(raw))
                tools, raw = body.get("tools"), json.dumps(body).encode()
            except Exception:
                pass
        self._forward(raw, tools)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("SHIM_PORT", "8001"))
    print(f"oai shim on 0.0.0.0:{port} -> {UPSTREAM}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
