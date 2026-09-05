from datetime import datetime, timezone

import pytest

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
        max_cross_sell_percentage=20,
        buyer_goal="Buy a compact Android USB-C charger",
        created_at=NOW,
        expires_in_minutes=1_440,
    )

    def fake_plan(**kwargs):
        eligible = {product.sku for product in kwargs["base_products"]}
        assert "CHG-20W-001" in eligible
        return DelegatedBuyerPlan(
            base_product_sku="CHG-20W-001",
            cross_sell_product_sku=None,
            reason="Compact eligible charger with a lower total cost.",
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
    assert result.quote.total_paise == 79_900
    assert result.checkout_confirmation_required is True

    execution = get_mandate_execution_state(result.execution_id)
    assert execution is not None
    assert execution.status == "quote_ready"
    assert execution.session_id == result.session_id
    assert execution.quote_id == result.quote.quote_id
    assert execution.committed_paise == result.quote.total_paise

    with pytest.raises(MandateAlreadyReservedError):
        run_delegated_purchase(
            mandate_id=mandate.mandate_id,
            task="Try to spend the same mandate again",
        )
