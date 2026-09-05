from datetime import datetime, timezone

import pytest

from backend.app.mandate_execution_ledger import (
    MandateAlreadyConsumedError,
    bind_execution_quote,
    bind_execution_session,
    consume_mandate_execution_for_quote,
    get_mandate_execution_state,
    release_mandate_execution,
    reserve_mandate_execution,
)
from backend.app.purchase_mandate_service import create_purchase_mandate


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "execution-ledger.db"),
    )


def make_mandate():
    return create_purchase_mandate(
        budget_paise=200_000,
        allowed_categories=["chargers"],
        required_compatibility=["usb-c"],
        buyer_goal="Buy a compact USB-C charger",
        created_at=NOW,
        expires_in_minutes=60,
    )


def test_execution_lifecycle_is_append_only_and_single_use() -> None:
    mandate = make_mandate()
    execution = reserve_mandate_execution(
        mandate.mandate_id,
        created_at=NOW,
    )

    execution = bind_execution_session(
        execution_id=execution.execution_id,
        session_id="session_00000000000000000000000000000001",
    )
    execution = bind_execution_quote(
        execution_id=execution.execution_id,
        quote_id="quote_00000000000000000000000000000001",
        amount_paise=129_900,
    )
    execution = consume_mandate_execution_for_quote(
        "quote_00000000000000000000000000000001"
    )

    assert execution is not None
    assert execution.status == "consumed"
    assert execution.committed_paise == 129_900
    assert get_mandate_execution_state(
        execution.execution_id
    ) == execution

    with pytest.raises(MandateAlreadyConsumedError):
        reserve_mandate_execution(
            mandate.mandate_id,
            created_at=NOW,
        )


def test_released_execution_allows_a_safe_retry() -> None:
    mandate = make_mandate()
    first = reserve_mandate_execution(
        mandate.mandate_id,
        created_at=NOW,
    )
    released = release_mandate_execution(first.execution_id)
    assert released.status == "released"

    second = reserve_mandate_execution(
        mandate.mandate_id,
        created_at=NOW,
    )
    assert second.execution_id != first.execution_id
    assert second.status == "reserved"
