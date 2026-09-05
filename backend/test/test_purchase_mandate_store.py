from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.models import PurchaseMandate
from backend.app.purchase_mandate_store import (
    PurchaseMandateConflictError,
    get_purchase_mandate,
    save_purchase_mandate,
)


MANDATE_ID = "mandate_00000000000000000000000000000001"
CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "mandates.db"),
    )


def make_mandate() -> PurchaseMandate:
    return PurchaseMandate(
        mandate_id=MANDATE_ID,
        budget_paise=200_000,
        allowed_categories=["chargers", "chargers"],
        required_compatibility=["usb-c"],
        max_cross_sell_percentage=20,
        checkout_confirmation_required=True,
        buyer_goal="  Buy a compatible charger  ",
        created_at=CREATED_AT,
        expires_at=CREATED_AT + timedelta(minutes=30),
    )


def test_mandate_normalizes_and_freezes_buyer_terms() -> None:
    mandate = make_mandate()

    assert mandate.allowed_categories == ("chargers",)
    assert mandate.required_compatibility == ("usb-c",)
    assert mandate.buyer_goal == "Buy a compatible charger"

    with pytest.raises(ValidationError):
        mandate.budget_paise = 300_000


def test_mandate_cannot_disable_checkout_confirmation() -> None:
    payload = make_mandate().model_dump()
    payload["checkout_confirmation_required"] = False

    with pytest.raises(ValidationError):
        PurchaseMandate.model_validate(payload)


def test_mandate_requires_future_timezone_aware_expiry() -> None:
    payload = make_mandate().model_dump()
    payload["expires_at"] = CREATED_AT

    with pytest.raises(ValidationError, match="after creation"):
        PurchaseMandate.model_validate(payload)

    payload["expires_at"] = datetime(2026, 1, 2)
    with pytest.raises(ValidationError, match="timezone"):
        PurchaseMandate.model_validate(payload)


def test_store_is_insert_only_and_round_trips() -> None:
    mandate = make_mandate()

    assert save_purchase_mandate(mandate) == mandate
    assert get_purchase_mandate(MANDATE_ID) == mandate

    with pytest.raises(PurchaseMandateConflictError):
        save_purchase_mandate(mandate)


def test_unknown_mandate_returns_none() -> None:
    assert get_purchase_mandate(MANDATE_ID) is None
