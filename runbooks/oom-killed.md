# Container killed (exit 137, out of memory)
Symptoms: `ops__docker_ps` shows `Exited (137)`; `ops__journal_tail {"unit": "docker", "lines": 60}` or the kernel
journal mentions `Out of memory: Killed process` / `oom-kill`. This host has unified CPU+GPU memory and no swap, so a
large model load or a compile job can push everything else out.
Steps: 1) `ops__disk_usage` and `ops__top_processes` to see what is holding memory right now.
2) If a heavy process (model server, compiler, backup) is running, the container died as collateral: start it again
with `ops__docker_start` and verify.
3) If it is killed again within a minute, the host is genuinely short of memory. Do not keep restarting it.
4) Report which process holds the memory; freeing it is a host-level decision outside this agent's scope.
