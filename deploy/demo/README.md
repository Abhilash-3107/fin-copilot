# Demo instance deployment

One small VPS (~$5), one container, seeded sample data only.
The API refuses statement uploads, backups, delete-everything, and AI-config writes when `FC_DEMO_MODE=1` - the box is structurally incapable of holding real user data.

## Setup

```bash
# On the VPS (Docker + Caddy installed):
docker build -t fc-demo deploy/demo/
docker run -d --name fc-demo --restart unless-stopped -p 127.0.0.1:8080:8080 fc-demo

# Caddy: copy Caddyfile to /etc/caddy/Caddyfile (edit the domain), then
systemctl reload caddy

# Cron (crontab -e):
0 * * * *  CONTAINER=fc-demo /srv/finance-copilot/reset.sh
0 4 * * 1  CONTAINER=fc-demo FC_DEMO_RESEED=1 /srv/finance-copilot/reset.sh
```

## Upgrading

```bash
docker build --no-cache -t fc-demo deploy/demo/   # pulls the latest PyPI release
docker rm -f fc-demo && docker run -d --name fc-demo --restart unless-stopped -p 127.0.0.1:8080:8080 fc-demo
```

## Notes

- LLM and embedding providers are `none` on this box: no API key to leak, no Ollama to run.
  The seeded dataset ships pre-annotated with realistic sources and confidences, so the review queue, Money Map, and dev-mode reasoning all demo well.
- No accounts, no cookies beyond what the app already uses, no analytics.
  Count visits and upload-attempt 403s from the Caddy/app logs; the 403s are the conversion signal.
- `rate_limit` requires the Caddy rate-limit plugin (`xcaddy build --with github.com/mholt/caddy-ratelimit`); drop that block if you use stock Caddy.
