# Disk full (No space left on device)
Symptoms: 500 errors, logs contain `No space left on device`, `df -h` shows a filesystem at 100 %.
Steps: 1) `df -h` inside the container. 2) Find large files under /var/cache or /tmp (`du -sh /var/cache/nginx /tmp`).
3) Remove temporary files only, never configuration or data. 4) Restart the service if it stopped. 5) Verify.
