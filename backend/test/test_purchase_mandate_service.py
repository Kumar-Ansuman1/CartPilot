from datetime import datetime, timedelta, timezone

import pytest

from backend.app.audit_events import list_mandate_audit_events
from backend.app.models import Product
from backend.app.purchase_mandate_service import (
    PurchaseMandateExpiredError,
    PurchaseMandateNotFoundError,
    PurchaseMandatePolicyError,
    authorize_product_under_mandate,
    create_purchase_mandate,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "mandate-service.db"),
    )


def make_product(**changes) -> Product:
    data = {
        "sku": "CHG-001",
        "name": "USB-C Charger",
        "description": "Compact fast charger",
        "category": "chargers",
        "price_paise": 100_000,
        "stock": 10,
        "tags": ["compact"],
        "compatibility_tags": ["usb-c", "android"],
        "cross_sell_skus": [],
        "active": True,
    }
    data.update(changes)
    return Product.model_validate(data)


def make_mandate(**changes):
    data = {
        "budget_paise": 200_000,
        "allowed_categories": ["chargers"],
        "required_compatibility": ["usb-c"],
        "max_cross_sell_percentage": 20,
        "expires_in_minutes": 30,
        "buyer_goal": "Buy a USB-C charger",
        "created_at": NOW,
    }
    data.update(changes)
    return create_purchase_mandate(**data)


def test_creation_is_audited_and_product_is_accepted() -> None:
    mandate = make_mandate()

    result = authorize_product_under_mandate(
        mandate_id=mandate.mandate_id,
        product=make_product(),
        evaluated_at=NOW + timedelta(minutes=1),
    )

    assert result.authorized is True
    assert [event.event_type for event in list_mandate_audit_events(
        mandate.mandate_id
    )] == ["mandate_created", "mandate_accepted"]


@pytest.mark.parametrize(
    ("product_changes", "current_total", "is_cross_sell", "reason"),
    [
        ({"category": "cables"}, 0, False, "CATEGORY_NOT_ALLOWED"),
        ({"compatibility_tags": ["lightning"]}, 0, False,
         "COMPATIBILITY_NOT_SATISFIED"),
        ({"price_paise": 250_000}, 0, False, "BUDGET_EXCEEDED"),
        ({"active": False}, 0, False, "PRODUCT_UNAVAILABLE"),
        ({"price_paise": 30_000}, 100_000, True,
         "CROSS_SELL_LIMIT_EXCEEDED"),
    ],
)
def test_policy_violations_are_rejected_and_audited(
    product_changes,
    current_total,
    is_cross_sell,
    reason,
) -> None:
    mandate = make_mandate()

    with pytest.raises(PurchaseMandatePolicyError) as error:
        authorize_product_under_mandate(
            mandate_id=mandate.mandate_id,
            product=make_product(**product_changes),
            current_total_paise=current_total,
            is_cross_sell=is_cross_sell,
            evaluated_at=NOW + timedelta(minutes=1),
        )

    assert error.value.reason_code == reason
    events = list_mandate_audit_events(mandate.mandate_id)
    assert [event.event_type for event in events[-2:]] == [
        "mandate_policy_violated",
        "mandate_rejected",
    ]
    assert events[-2].reason_code == reason


def test_expired_mandate_rejects_and_audits_attempt() -> None:
    mandate = make_mandate(expires_in_minutes=1)

    with pytest.raises(PurchaseMandateExpiredError):
        authorize_product_under_mandate(
            mandate_id=mandate.mandate_id,
            product=make_product(),
            evaluated_at=NOW + timedelta(minutes=1),
        )

    assert [event.event_type for event in list_mandate_audit_events(
        mandate.mandate_id
    )][-2:] == ["mandate_expired", "mandate_rejected"]


def test_unknown_mandate_rejects_authorization() -> None:
    with pytest.raises(PurchaseMandateNotFoundError):
        authorize_product_under_mandate(
            mandate_id="mandate_00000000000000000000000000000001",
            product=make_product(),
            evaluated_at=NOW,
        )


def test_creation_rejects_excessive_validity() -> None:
    with pytest.raises(ValueError, match="between 1 and 1440"):
        make_mandate(expires_in_minutes=1_441)
