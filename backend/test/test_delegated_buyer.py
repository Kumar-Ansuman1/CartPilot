from datetime import datetime, timezone

import pytest

from backend.app.audit_events import list_audit_events
from backend.app.delegated_buyer import (
    DelegatedBuyerPlan,
    run_delegated_purchase,
)
from backend.app.mandate_execution_ledger import (
    MandateAlreadyReservedError,
    get_mandate_execution_state,
)
from backend.app.purchase_mandate_service import create_purchase_mandate


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "delegated-buyer.db"),
    )


def test_ai_plan_is_revalidated_and_quote_is_mandate_bound(monkeypatch) -> None:
    mandate = create_purchase_mandate(
        budget_paise=200_000,
        allowed_categories=["chargers"],
        required_compatibility=["usb-c", "android"],
        max_cross_sell_percentage=0,
        buyer_goal="Buy a compact Android USB-C charger",
        created_at=NOW,
        expires_in_minutes=1_440,
    )

    def fake_plan(**kwargs):
        eligible = {product.sku for product in kwargs["base_products"]}
        assert "CHG-20W-001" in eligible
        assert kwargs["cross_sells"]["CHG-20W-001"] == []
        return DelegatedBuyerPlan(
            base_product_sku="CHG-20W-001",
            cross_sell_product_sku=None,
            reason="Compact eligible charger; cross-sell is disabled by the mandate.",
            confidence=0.91,
        )

    monkeypatch.setattr(
        "backend.app.delegated_buyer._plan_with_ai",
        fake_plan,
    )

    result = run_delegated_purchase(
        mandate_id=mandate.mandate_id,
        task="Pick a compact charger for my Android phone",
    )

    assert result.status == "purchase_ready_for_confirmation"
    assert result.base_product.sku == "CHG-20W-001"
    assert result.cross_sell_product is None
    assert result.quote.total_paise == 79_900
    assert result.checkout_confirmation_required is True

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "quote_ready"
    assert execution.session_id == result.session_id
    assert execution.quote_id == result.quote.quote_id
    assert execution.committed_paise == result.quote.total_paise

    audit_events = list_audit_events(result.session_id)
    cross_sell_events = [
        event for event in audit_events
        if event.event_type == "cross_sell_decided"
    ]
    assert len(cross_sell_events) == 1
    assert cross_sell_events[0].reason_code == "NO_ELIGIBLE_CROSS_SELL"

    with pytest.raises(MandateAlreadyReservedError):
        run_delegated_purchase(
            mandate_id=mandate.mandate_id,
            task="Try to spend the same mandate again",
        )


def test_ai_can_select_eligible_cross_sell_and_quote_includes_it(monkeypatch) -> None:
    mandate = create_purchase_mandate(
        budget_paise=200_000,
        allowed_categories=["chargers"],
        required_compatibility=["usb-c", "android"],
        max_cross_sell_percentage=20,
        buyer_goal="Buy a compact charger with a useful charging companion",
        created_at=NOW,
        expires_in_minutes=1_440,
    )

    def fake_plan(**kwargs):
        eligible_base = {product.sku for product in kwargs["base_products"]}
        assert "CHG-20W-001" in eligible_base

        eligible_cross_sells = {
            product.sku
            for product in kwargs["cross_sells"]["CHG-20W-001"]
        }
        assert "CBL-C2C-002" in eligible_cross_sells

        return DelegatedBuyerPlan(
            base_product_sku="CHG-20W-001",
            cross_sell_product_sku="CBL-C2C-002",
            reason=(
                "The compact charger fits the goal and the eligible USB-C "
                "cable is a useful companion within the mandate."
            ),
            confidence=0.94,
        )

    monkeypatch.setattr(
        "backend.app.delegated_buyer._plan_with_ai",
        fake_plan,
    )

    result = run_delegated_purchase(
        mandate_id=mandate.mandate_id,
        task="Choose a compact charger and include a useful companion",
    )

    assert result.base_product.sku == "CHG-20W-001"
    assert result.cross_sell_product is not None
    assert result.cross_sell_product.sku == "CBL-C2C-002"
    assert result.quote.base_price_paise == 79_900
    assert result.quote.upsell_price_paise == 19_900
    assert result.quote.total_paise == 99_800

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "quote_ready"
    assert execution.committed_paise == 99_800

    audit_events = list_audit_events(result.session_id)
    cross_sell_events = [
        event for event in audit_events
        if event.event_type == "cross_sell_decided"
    ]
    assert len(cross_sell_events) == 1
    assert cross_sell_events[0].reason_code == "AI_SELECTED_ELIGIBLE_CROSS_SELL"
    assert cross_sell_events[0].sku == "CBL-C2C-002"
