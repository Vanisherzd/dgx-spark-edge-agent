# nginx fails to start: configuration error
Symptoms: container `edge-victim` exits immediately after start; logs show `nginx: [emerg] ... in /etc/nginx/conf.d/default.conf:<line>`
(unexpected token, unknown directive, missing semicolon).
Steps: 1) `docker logs edge-victim --tail 20` and read the [emerg] line and line number. 2) Read the config file at
`victim/conf.d/default.conf`. 3) Fix the syntax at that line (each directive ends with `;`, braces balanced), write it
back. 4) `docker restart edge-victim`. 5) Verify the health URL returns 200 and `/api/` still proxies.
