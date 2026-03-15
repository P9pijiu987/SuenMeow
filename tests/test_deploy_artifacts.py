from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_exposes_web_port() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "EXPOSE 8000" in dockerfile
    assert 'CMD ["python", "main.py", "worker", "--root", "/app"]' in dockerfile


def test_base_compose_defines_web_and_worker_services() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "suenmeow-web:" in compose
    assert "suenmeow-worker:" in compose
    assert 'command: ["python", "main.py", "web", "--root", "/app"]' in compose
    assert 'command: ["python", "main.py", "worker", "--root", "/app"]' in compose
    assert '- "${SUENMEOW_WEB_PORT:-8000}:8000"' in compose
    assert "healthcheck:" in compose


def test_prod_compose_makes_config_read_only() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "suenmeow-web:" in compose
    assert "suenmeow-worker:" in compose
    assert "SUENMEOW_ENV: production" in compose
    assert compose.count("./config:/app/config:ro") == 2


def test_webui_default_host_is_container_friendly() -> None:
    webui = (ROOT / "config" / "webui.toml").read_text(encoding="utf-8")

    assert 'host = "0.0.0.0"' in webui


def test_deploy_docs_cover_restart_validation_and_rollback() -> None:
    deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")

    assert "Restart / persistence validation" in deploy
    assert "Rollback playbook" in deploy
    assert "docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build" in deploy


def test_deploy_docs_cover_persistent_state_artifacts() -> None:
    deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")

    assert "data/suenmeow.sqlite3" in deploy
    assert "logs/latest.log" in deploy
    assert "config/" in deploy
