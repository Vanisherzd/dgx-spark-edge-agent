# 504 Gateway Timeout on /api/
Symptoms: `/` returns 200, `/api/` returns 504 (not 502). nginx logs show `upstream timed out (110: Connection timed
out) while reading response header from upstream`. 502 means the upstream refused the connection; 504 means it
accepted or never answered in time, so check that the upstream is alive before touching any config.
Steps: 1) `ops__check_health` to confirm 200 on `/` and 504 on `/api/`.
2) `ops__docker_ps`: if `edge-victim-app` is Exited, this is really the upstream-down case, `ops__docker_start` it.
3) If it is Up, `ops__docker_logs {"name": "edge-victim-app", "tail": 40}` for errors, and `ops__top_processes` to see
whether it is spinning on CPU.
4) `ops__read_config` and check the `/api/` `proxy_pass` target and any `proxy_read_timeout`.
5) Restart the upstream (`ops__docker_restart {"name": "edge-victim-app"}`), then verify `/api/` returns 200.
Do not restart the front-end for an upstream timeout.
