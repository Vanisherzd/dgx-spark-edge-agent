"""Fault-injection harness on two sandbox containers (never touches the Spark host services).

  uv run --no-sync scripts/faults.py setup            create edge-victim (nginx front, 127.0.0.1:8880) + edge-victim-app (upstream)
  uv run --no-sync scripts/faults.py inject <case>    cases: nginx-stopped | bad-config | upstream-down | bad-upstream-name | container-removed
  uv run --no-sync scripts/faults.py verify           exit 0 when / and /api/ both return 200
  uv run --no-sync scripts/faults.py reset            restore config, start both containers
  uv run --no-sync scripts/faults.py run <case>       inject -> agent.py -> verify   (the end-to-end test)
"""
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONF_DIR = ROOT / "victim" / "conf.d"
# Canonical config lives in the script, not on disk: the agent may rewrite the file in its own style, and the fault
# injection must still produce a broken file afterwards.
GOOD_CONF = """server {
    listen 80;
    server_name _;
    location / { return 200 "ok\\n"; add_header Content-Type text/plain; }
    location /api/ { proxy_pass http://edge-victim-app:80/; proxy_connect_timeout 2s; proxy_read_timeout 5s; }
}
"""
FRONT, APP, NET = "edge-victim", "edge-victim-app", "edge-net"
BAD_CONF = GOOD_CONF.replace('return 200 "ok\\n";', 'return 200 "ok\\n"')  # drop one semicolon -> [emerg]
# A fault the agent has not been drilled on: the upstream name is wrong, so nginx exits with "host not found in
# upstream". Fixing it needs the runbook (or real reasoning), not the docker_start reflex that solves the other cases.
BAD_NAME_CONF = GOOD_CONF.replace("http://edge-victim-app:80/", "http://edge-victim-backend:80/")
assert BAD_CONF != GOOD_CONF and BAD_NAME_CONF != GOOD_CONF


def sh(cmd, check=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        sys.exit(f"command failed: {cmd}\n{r.stderr}")
    return r


def http(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8880{path}", timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


# One definition of each container, shared with the agent's host-layer repair tools (scripts/agent.py).
SPECS = {
    APP: f"docker run -d --name {APP} --network {NET} --restart no nginx:latest",
    FRONT: (f"docker run -d --name {FRONT} --network {NET} --restart no -p 127.0.0.1:8880:80 "
            f"-v {CONF_DIR}:/etc/nginx/conf.d:ro nginx:latest"),
}


def exists(name):
    return bool(sh(f"docker ps -aq --filter name=^{name}$").stdout.strip())


def ensure(name):
    """Create a container that is gone entirely. Start/restart is the agent's job; this is the missing-container case."""
    if name not in SPECS:
        return f"denied: {name!r} is not part of this stack"
    if exists(name):
        return f"{name} already exists"
    if name == FRONT:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        if not (CONF_DIR / "default.conf").exists():
            (CONF_DIR / "default.conf").write_text(GOOD_CONF)
    sh(f"docker network create {NET} 2>/dev/null")
    r = sh(SPECS[name])
    return f"created {name}" if not r.returncode else f"failed to create {name}: {(r.stderr or r.stdout).strip()[:300]}"


def setup():
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    (CONF_DIR / "default.conf").write_text(GOOD_CONF)   # the file is not tracked by git; drills rewrite it
    sh(f"docker network create {NET} 2>/dev/null")
    sh(f"docker rm -f {FRONT} {APP} >/dev/null 2>&1")
    for name in (APP, FRONT):
        sh(SPECS[name], check=True)
    time.sleep(2)
    print("setup:", "/", http("/"), "/api/", http("/api/"))


def reset():
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    (CONF_DIR / "default.conf").write_text(GOOD_CONF)
    for name in (APP, FRONT):       # a previous drill may have removed one; start cannot bring back what is gone
        ensure(name)
    sh(f"docker start {APP} {FRONT} >/dev/null 2>&1")
    sh(f"docker restart {FRONT} >/dev/null 2>&1")
    time.sleep(2)


def inject(case):
    if case == "nginx-stopped":
        sh(f"docker stop -t 1 {FRONT}", check=True)
    elif case == "bad-config":
        (CONF_DIR / "default.conf").write_text(BAD_CONF)
        sh(f"docker restart -t 1 {FRONT}")  # nginx refuses the config and the container exits
    elif case == "upstream-down":
        sh(f"docker stop -t 1 {APP}", check=True)
    elif case == "container-removed":
        sh(f"docker rm -f {FRONT}", check=True)     # start/restart cannot fix this; the container has to be recreated
    elif case == "bad-upstream-name":
        (CONF_DIR / "default.conf").write_text(BAD_NAME_CONF)
        sh(f"docker restart -t 1 {FRONT}")  # nginx cannot resolve the name and exits
    else:
        sys.exit(f"unknown case {case}")
    time.sleep(2)
    print(f"injected {case}:", "/", http("/"), "/api/", http("/api/"))


def verify():
    ok = http("/") == 200 and http("/api/") == 200
    print("verify:", "/", http("/"), "/api/", http("/api/"), "->", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "setup":
        setup()
    elif cmd == "reset":
        reset(); verify()
    elif cmd == "inject":
        inject(sys.argv[2])
    elif cmd == "verify":
        sys.exit(0 if verify() else 1)
    elif cmd == "run":
        case = sys.argv[2]
        reset(); inject(case)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "agent.py"), "--case", case])
        ok = verify()
        print(f"RESULT {case}: {'PASS' if ok else 'FAIL'} (agent exit {r.returncode})")
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
