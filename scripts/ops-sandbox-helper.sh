#!/bin/sh
# ops: the only way to touch the web stack from inside the sandbox. Talks to the host ops API.
#   ops tools                      list tools
#   ops call <tool> ['<json args>'] run a tool, e.g.  ops call docker_logs '{"name":"edge-victim","tail":30}'
API="${OPS_API:-http://host.openshell.internal:8790}"
case "$1" in
  tools) curl -s -m 20 "$API/tools" | python3 -c 'import sys,json
for t in json.load(sys.stdin)["tools"]:
    print("- %s: %s  args=%s" % (t["name"], t["description"], json.dumps(t["parameters"].get("properties", {}))))' ;;
  call)  [ -n "$2" ] || { echo "usage: ops call <tool> ['<json args>']" >&2; exit 2; }
         python3 - "$API" "$2" "${3:-{\}}" <<'PY'
import json, sys, urllib.request
api, tool, args = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.dumps({"name": tool, "args": json.loads(args)}).encode()
req = urllib.request.Request(api + "/call", data=body, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print(json.load(r).get("result", ""))
except urllib.error.HTTPError as e:
    print(f"ops error {e.code}: {e.read().decode(errors='replace')[:400]}"); sys.exit(1)
PY
         ;;
  *) echo "usage: ops tools | ops call <tool> ['<json args>']" >&2; exit 2 ;;
esac
