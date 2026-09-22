# Web service down (connection refused / no response)
Symptoms: health check on the front-end URL fails with connection refused or timeout; `docker ps` shows the
`edge-victim` container Exited.
Steps: 1) `docker ps -a` to confirm the container state. 2) `docker logs edge-victim --tail 50` for the exit reason.
3) If it exited without a config error, start it again (`docker start edge-victim`). 4) Re-check the health URL.
