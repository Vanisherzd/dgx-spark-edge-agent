# Container restart loop (starts, exits, starts again)
Symptoms: `ops__docker_ps` shows `Restarting (N) X seconds ago`, or the same container shows a fresh `Up 1 second`
every time you look. Health URLs flap between 200 and unreachable.
Steps: 1) `ops__docker_logs {"name": "<container>", "tail": 60}` and read the last error before each exit; the exit
code in `docker_ps` narrows it (1 = application/config error, 137 = killed, 139 = segfault).
2) A config error repeats identically on every restart: fix the cause (see nginx-config-error), do not just restart.
3) Exit 137 means it was killed, usually out of memory: see oom-killed.
4) A dependency that is not up yet (upstream, database) also loops: start the dependency first, then the dependent.
5) Restarting a crash loop without changing anything is not a fix. If the logs show no cause you can act on, stop and
escalate with the exit code and the last 20 log lines.
