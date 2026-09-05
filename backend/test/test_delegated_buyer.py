from datetime import datetime, timezone

import pytest

from backend.app.audit_events import list_audit_events
from backend.app.delegated_buyer import (
    DelegatedBuyerPlan,
    finalize_delegated_quote,
    run_delegated_purchase,
)
from backend.app.mandate_execution_ledger import (
    MandateAlreadyReservedError,
    get_mandate_execution_state,
)
from backend.app.purchase_mandate_service import create_purchase_mandate
from backend.app.shopping_session_store import get_shopping_session


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "delegated-buyer.db"),
    )


def test_delegated_selection_waits_for_buyer_before_quote(monkeypatch) -> None:
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
            reason="Compact eligible charger; no eligible companion is available.",
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

    assert result.status == "selection_ready_for_confirmation"
    assert result.base_product.sku == "CHG-20W-001"
    assert result.cross_sell_product is None
    assert result.checkout_confirmation_required is True

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "reserved"
    assert execution.session_id == result.session_id
    assert execution.quote_id is None
    assert execution.committed_paise is None

    session = get_shopping_session(result.session_id)
    assert session is not None
    assert session.status == "awaiting_cross_sell_decision"
    assert session.cross_sell_option_skus == []

    quote = finalize_delegated_quote(
        execution_id=result.execution_id,
        include_cross_sell=False,
    )
    assert quote.total_paise == 79_900
    assert quote.upsell_product_sku is None

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "quote_ready"
    assert execution.quote_id == quote.quote_id
    assert execution.committed_paise == 79_900

    with pytest.raises(MandateAlreadyReservedError):
        run_delegated_purchase(
            mandate_id=mandate.mandate_id,
            task="Try to spend the same mandate again",
        )


def _run_cross_sell_recommendation(monkeypatch):
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

    return run_delegated_purchase(
        mandate_id=mandate.mandate_id,
        task="Choose a compact charger and recommend a useful companion",
    )


def test_ai_recommendation_is_not_automatically_added(monkeypatch) -> None:
    result = _run_cross_sell_recommendation(monkeypatch)

    assert result.base_product.sku == "CHG-20W-001"
    assert result.cross_sell_product is not None
    assert result.cross_sell_product.sku == "CBL-C2C-002"

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "reserved"
    assert execution.quote_id is None
    assert execution.committed_paise is None

    session = get_shopping_session(result.session_id)
    assert session is not None
    assert session.status == "awaiting_cross_sell_decision"
    assert session.cross_sell_option_skus == ["CBL-C2C-002"]

    quote = finalize_delegated_quote(
        execution_id=result.execution_id,
        include_cross_sell=False,
    )
    assert quote.base_price_paise == 79_900
    assert quote.upsell_product_sku is None
    assert quote.upsell_price_paise == 0
    assert quote.total_paise == 79_900

    audit_events = list_audit_events(result.session_id)
    reason_codes = {event.reason_code for event in audit_events}
    assert "AI_RECOMMENDED_ELIGIBLE_CROSS_SELL" in reason_codes
    assert "BUYER_DECLINED_AI_CROSS_SELL" in reason_codes


def test_checked_ai_recommendation_is_included_in_final_quote(monkeypatch) -> None:
    result = _run_cross_sell_recommendation(monkeypatch)

    quote = finalize_delegated_quote(
        execution_id=result.execution_id,
        include_cross_sell=True,
    )

    assert quote.base_price_paise == 79_900
    assert quote.upsell_product_sku == "CBL-C2C-002"
    assert quote.upsell_price_paise == 19_900
    assert quote.total_paise == 99_800

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "quote_ready"
    assert execution.committed_paise == 99_800

    audit_events = list_audit_events(result.session_id)
    reason_codes = {event.reason_code for event in audit_events}
    assert "AI_RECOMMENDED_ELIGIBLE_CROSS_SELL" in reason_codes
    assert "BUYER_ACCEPTED_AI_CROSS_SELL" in reason_codes
