---
name: ops
description: Inspect and repair the web stack (edge-victim nginx front-end + edge-victim-app upstream) through the host ops API. Use for any alert about the web stack, 502/504, unreachable health URLs, or a container that is down.
---
# ops — the only way to touch the web stack

Your shell has no docker and no systemctl. Every action goes through one command:

```
/sandbox/bin/ops tools                                   # list tools + argument schema
/sandbox/bin/ops call <tool> '<json args>'                # run one tool
```

Tools: check_health, docker_ps, docker_logs {"name","tail"}, docker_exec {"name","cmd"}, docker_start {"name"},
docker_restart {"name"}, read_config, write_config {"content"}, search_runbooks {"query"}, self_heal.
Container names: edge-victim (nginx front-end), edge-victim-app (upstream).

Procedure: 1) check_health 2) docker_ps 3) docker_logs of the exited/unhealthy container 4) search_runbooks
5) fix with the least invasive action (docker_start; or write_config then docker_restart edge-victim) 6) check_health
again until both URLs are 200. If you get stuck, `/sandbox/bin/ops call self_heal '{}'` runs the full repair loop.

Examples:
```
/sandbox/bin/ops call docker_logs '{"name":"edge-victim","tail":30}'
/sandbox/bin/ops call docker_start '{"name":"edge-victim-app"}'
```
