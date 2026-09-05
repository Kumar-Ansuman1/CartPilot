import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "mandate-api.db"),
    )


def valid_payload() -> dict[str, object]:
    return {
        "budget_paise": 200_000,
        "allowed_categories": ["chargers"],
        "required_compatibility": ["usb-c"],
        "max_cross_sell_percentage": 20,
        "expires_in_minutes": 30,
        "checkout_confirmation_required": True,
        "buyer_goal": "Buy a USB-C charger",
    }


def test_create_read_and_audit_mandate() -> None:
    create_response = client.post(
        "/api/mandates",
        json=valid_payload(),
    )

    assert create_response.status_code == 201
    mandate = create_response.json()
    mandate_id = mandate["mandate_id"]
    assert mandate["checkout_confirmation_required"] is True

    read_response = client.get(f"/api/mandates/{mandate_id}")
    assert read_response.status_code == 200
    assert read_response.json() == mandate
    assert read_response.headers["cache-control"] == "no-store"

    audit_response = client.get(
        f"/api/mandates/{mandate_id}/audit"
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["events"][0]["event_type"] == (
        "mandate_created"
    )


def test_api_cannot_disable_checkout_confirmation() -> None:
    payload = valid_payload()
    payload["checkout_confirmation_required"] = False

    response = client.post("/api/mandates", json=payload)

    assert response.status_code == 422


def test_api_rejects_empty_allowed_categories() -> None:
    payload = valid_payload()
    payload["allowed_categories"] = []

    response = client.post("/api/mandates", json=payload)

    assert response.status_code == 422


def test_unknown_mandate_returns_not_found() -> None:
    mandate_id = "mandate_00000000000000000000000000000001"

    assert client.get(f"/api/mandates/{mandate_id}").status_code == 404
    assert client.get(
        f"/api/mandates/{mandate_id}/audit"
    ).status_code == 404
