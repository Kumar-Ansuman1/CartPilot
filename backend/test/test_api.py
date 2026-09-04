from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.commerce_agent import CommerceAgentResult
from backend.app.main import app
from backend.app.models import ExtractedShoppingIntent


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_shop_endpoint_returns_clarification():
    intent = ExtractedShoppingIntent(
        search_query="protective phone case",
        budget_rupees=None,
        requested_categories=["cases"],
        compatibility_tags=[],
        needs_clarification=True,
        clarification_question="What is your phone model and budget?",
    )

    result = CommerceAgentResult(
        status="clarification_required",
        message="What is your phone model and budget?",
        intent=intent,
        decision_trace=[
            "The request was stopped before catalog search.",
            "No quote or payment action was created.",
        ],
    )

    with patch(
        "backend.app.main.run_commerce_agent",
        return_value=result,
    ):
        response = client.post(
            "/api/shop",
            json={"message": "I need a phone case"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "clarification_required"
    assert response.json()["quote"] is None


def test_shop_endpoint_rejects_short_message():
    response = client.post(
        "/api/shop",
        json={"message": "hi"},
    )

    assert response.status_code == 422


def test_shop_endpoint_handles_ai_failure_safely():
    with patch(
        "backend.app.main.run_commerce_agent",
        side_effect=RuntimeError("Provider failure details"),
    ):
        response = client.post(
            "/api/shop",
            json={"message": "Find me a USB-C charger"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The AI intent service is temporarily unavailable."
    }
    assert "Provider failure details" not in response.text