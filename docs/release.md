# Releasing Finance Copilot

The steps below need accounts or hardware only the maintainer has.
Everything that can be checked from the repo was checked on 2026-11-01: the wheel builds with the UI bundled, the full test suite passes against the installed wheel, and the smoke step (`uvx --from <wheel> finance-copilot`, upload the fixture statement, assert parsed rows) passes on macOS.

## 1. Reserve the name on PyPI (once)

1. Sign in at pypi.org and open "Your account" > "Publishing" > "Add a new pending publisher".
2. Fill in: PyPI project name `finance-copilot`, owner `Abhilash-3107`, repository `fin-copilot`, workflow `release.yml`, environment `pypi`.
3. On GitHub, open the repository's Settings > Environments > "New environment" and name it `pypi`.
   Optionally add yourself as a required reviewer so every publish waits for a click.

No API token is created or stored: `release.yml` publishes with trusted publishing.

## 2. Cut the release

1. Make sure `CHANGELOG.md` describes the release and `version` in `pyproject.toml` matches it.
2. Push `main`, then tag and push the tag:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. Watch the "Release" workflow: build (wheel + tests against the installed wheel), smoke on Ubuntu, macOS, and Windows, then publish.
4. From a machine that has never run the project, check the real install:

   ```bash
   uvx finance-copilot
   ```

## 3. Windows check (manual, once per release)

CI's windows-latest smoke covers install, start, and upload.
On a real Windows machine also click through: upload a statement, run auto-annotate, download a backup, restore it (Settings > Data), and quit the app.

## 4. Demo instance

1. Provision a small VPS with Docker and Caddy, and point a DNS record (for example `demo.yourdomain.com`) at it.
2. Replace `demo.example.com` in `deploy/demo/Caddyfile` and in the "Try the demo" link in `site/index.html` with that domain.
3. Follow `deploy/demo/README.md` (build, run, Caddy, cron).
   The image installs the latest PyPI release, so publish first.
4. Check from a browser: the demo banner shows, "Load demo data" is not needed (the entrypoint seeds it), uploads are refused with the "this is the shared demo" message, and the Goa trip group reads "Everyone's settled up".
