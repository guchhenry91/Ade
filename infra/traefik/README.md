# Traefik routing bundle for OpenClaw

Domains:
- mission.henryopenclaw.cloud -> Mission Control (8787)
- canvas.henryopenclaw.cloud -> OpenClaw Gateway/Canvas (18789)

## Files
- dynamic/openclaw-routes.yml
- static/traefik.static.sample.yml

## Deploy on host Traefik
1. Copy `dynamic/openclaw-routes.yml` to Traefik dynamic config dir (e.g. `/etc/traefik/dynamic/`).
2. Ensure static Traefik config has:
   - `entryPoints.web` and `entryPoints.websecure`
   - file provider watching dynamic dir
   - Let's Encrypt resolver named `letsencrypt`
3. Ensure ports 80/443 are open to internet and DNS points to this server.
4. Reload/restart Traefik.

## Verification
- https://mission.henryopenclaw.cloud
- https://canvas.henryopenclaw.cloud
- https://mission.henryopenclaw.cloud/api/agents

## Notes
- OpenClaw gateway token auth must remain enabled (`gateway.auth.mode=token`).
- Canvas path remains available through gateway app routes.
