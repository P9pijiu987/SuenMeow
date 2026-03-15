# SuenMeow Deployment Guide

## 1. Deployment shape

SuenMeow runs as two long-lived processes:

- `suenmeow-web`: FastAPI admin UI and `/health` endpoint
- `suenmeow-worker`: trigger engine, polling loop, and pipeline execution

Both services use the same image and mount the same persistent directories:

- `config/` — TOML runtime configuration
- `data/` — SQLite database (`data/suenmeow.sqlite3`)
- `logs/` — rolling runtime logs (`logs/latest.log`)

## 2. Environment layout

### Local / staging

Use the base compose file:

```bash
docker compose up --build
```

### Production-like

Use the production override so config is read-only inside the containers:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## 3. Configuration notes

- Default web bind host is `0.0.0.0` so the containerized web service is reachable through published ports.
- External web port can be changed with `.env` / compose variable `SUENMEOW_WEB_PORT`.
- Path overrides are supported through:
  - `SUENMEOW_CONFIG_DIR`
  - `SUENMEOW_DATA_DIR`
  - `SUENMEOW_LOG_DIR`
- Relative override paths are resolved from the project root passed to `main.py --root`.

## 4. Health checks

- Container health is defined on `suenmeow-web`.
- Compose probes `http://127.0.0.1:8000/health` from inside the container.
- Expected response is HTTP `200` with JSON payload `{"status": "正常"}`.

## 5. First startup checklist

1. Copy `.env.example` to `.env` if you need a different published port or path overrides.
2. Review all files under `config/`.
3. Confirm `config/runtime.toml` is still in a safe mode before first live connection:
   - `read_only = true`
   - `allow_send_reply = false`
   - `require_approval_before_send = true`
4. Start the stack.
5. Open `http://localhost:${SUENMEOW_WEB_PORT:-8000}/health`.
6. Confirm `logs/latest.log` appears.
7. Confirm `data/suenmeow.sqlite3` appears.

## 6. Restart / persistence validation

Use this minimal validation after upgrades or host restarts:

1. `docker compose ps` shows both services running.
2. `docker compose logs --tail=100 suenmeow-web suenmeow-worker` shows normal startup with no crash loop.
3. `curl http://localhost:${SUENMEOW_WEB_PORT:-8000}/health` returns `{"status": "正常"}`.
4. Stop the stack: `docker compose down`.
5. Start it again.
6. Verify all of the following still exist after restart:
   - `config/`
   - `data/suenmeow.sqlite3`
   - `logs/latest.log`
7. Re-open the admin UI and confirm recent pipeline/admin state is still present.

## 7. Rolling config changes

- Edit TOML under `config/` on the host.
- Restart the affected service if the change is startup-only.
- Runtime flags and several planner/threshold settings already hot-reload inside the worker, but deployment-level changes should still be treated conservatively.

## 8. Rollback playbook

If a deployment is unhealthy:

1. Put the system back into a safe posture in `config/runtime.toml`:
   - `read_only = true`
   - `allow_send_reply = false`
   - `panic_switch = true` if you need to freeze trigger processing quickly
2. Restart containers with the last known good image / working tree.
3. Bring the stack up again:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

4. Check `/health` and `docker compose logs`.
5. Verify `data/suenmeow.sqlite3` and `logs/latest.log` are intact before re-enabling any send behavior.

## 9. Current limitations

- Worker health is indirect; there is no dedicated worker HTTP health endpoint yet.
- Production rollout still depends on the final verification wave (`F1`-`F4`) for end-to-end readiness review.
