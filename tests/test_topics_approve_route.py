from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes.topics import router


class _ApprovalServiceStub:
    def __init__(self, *, result: dict[str, object] | None = None, error: Exception | None = None) -> None:
        self._result: dict[str, object] | None = result
        self._error: Exception | None = error
        self.calls: list[int] = []

    async def approve_pending_reply(self, pending_reply_id: int) -> dict[str, object]:
        self.calls.append(pending_reply_id)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _create_app(approval_service: _ApprovalServiceStub) -> FastAPI:
    app = FastAPI()
    app.state.approval_service = approval_service
    app.include_router(router)
    return app


def test_approve_route_maps_keyerror_to_404() -> None:
    approval_service = _ApprovalServiceStub(error=KeyError("pending reply 999 not found"))
    app = _create_app(approval_service)

    with TestClient(app) as client:
        response = client.post("/topics/pending-replies/999/approve")

    assert response.status_code == 404
    assert response.json()["detail"] == "'pending reply 999 not found'"
    assert approval_service.calls == [999]


def test_approve_route_maps_runtimeerror_to_409() -> None:
    approval_service = _ApprovalServiceStub(error=RuntimeError("manual approval send is disabled"))
    app = _create_app(approval_service)

    with TestClient(app) as client:
        response = client.post("/topics/pending-replies/123/approve")

    assert response.status_code == 409
    assert response.json()["detail"] == "manual approval send is disabled"
    assert approval_service.calls == [123]


def test_approve_route_returns_item_on_success() -> None:
    approval_service = _ApprovalServiceStub(
        result={"id": 5, "topic_id": 42, "status": "sent", "reply_post_id": 88}
    )
    app = _create_app(approval_service)

    with TestClient(app) as client:
        response = client.post("/topics/pending-replies/5/approve")

    assert response.status_code == 200
    assert response.json() == {"id": 5, "topic_id": 42, "status": "sent", "reply_post_id": 88}
    assert approval_service.calls == [5]
