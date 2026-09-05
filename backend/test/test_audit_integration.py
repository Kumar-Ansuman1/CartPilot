from pathlib import Path

import pytest

import backend.app.commerce_agent as commerce_agent
import backend.app.selection_service as selection_service
from backend.app.audit_events import list_audit_events
from backend.app.catalog import Catalog
from backend.app.models import (
    ExtractedShoppingIntent,
    Product,
    ShoppingRequest,
)
from backend.app.shopping_session_store import (
    ShoppingSessionStateError,
    create_shopping_session,
)


def make_product(
    *,
    sku: str,
    name: str,
    category: str,
    price_paise: int,
    cross_sell_skus: list[str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        description="A product used for audit testing.",
        category=category,
        price_paise=price_paise,
        stock=5,
        tags=["usb-c"],
        compatibility_tags=["usb-c"],
        cross_sell_skus=cross_sell_skus or [],
        active=True,
    )


def make_catalog() -> Catalog:
    products = [
        make_product(
            sku="CHG-001",
            name="USB-C Charger One",
            category="chargers",
            price_paise=100_000,
            cross_sell_skus=[
                "CBL-002",
                "CBL-001",
            ],
        ),
        make_product(
            sku="CHG-002",
            name="USB-C Charger Two",
            category="chargers",
            price_paise=150_000,
        ),
        make_product(
            sku="CHG-003",
            name="USB-C Charger Three",
            category="chargers",
            price_paise=200_000,
        ),
        make_product(
            sku="CBL-001",
            name="USB-C Cable Basic",
            category="cables",
            price_paise=30_000,
        ),
        make_product(
            sku="CBL-002",
            name="USB-C Cable Plus",
            category="cables",
            price_paise=50_000,
        ),
    ]

    return Catalog(
        merchant_id="voltcart",
        merchant_name="VoltCart",
        catalog_version="test-v1",
        currency="INR",
        products={
            product.sku: product
            for product in products
        },
    )


def complete_intent() -> ExtractedShoppingIntent:
    return ExtractedShoppingIntent(
        search_query="usb-c charger",
        budget_rupees=3000,
        requested_categories=["chargers"],
        compatibility_tags=["usb-c"],
        needs_clarification=False,
        clarification_question=None,
    )


def make_request() -> ShoppingRequest:
    return ShoppingRequest(
        query="usb-c charger",
        budget_paise=300_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )


def create_test_session():
    return create_shopping_session(
        request=make_request(),
        catalog_version="test-v1",
        base_product_skus=[
            "CHG-001",
            "CHG-002",
            "CHG-003",
        ],
    )


def configure_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / name),
    )


def test_initial_agent_actions_are_audited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "initial-agent-audit.db",
    )
    monkeypatch.setattr(
        commerce_agent,
        "extract_shopping_intent",
        lambda _: complete_intent(),
    )
    monkeypatch.setattr(
        commerce_agent,
        "load_catalog",
        make_catalog,
    )

    buyer_message = "USB-C charger under 3000"
    result = commerce_agent.run_commerce_agent(
        buyer_message
    )

    assert result.session_id is not None

    events = list_audit_events(result.session_id)

    assert [event.event_type for event in events] == [
        "intent_extracted",
        "catalog_searched",
        "base_product_offered",
        "base_product_offered",
        "base_product_offered",
    ]
    assert events[0].actor == "ai"
    assert events[1].amount_paise == 300_000
    assert [event.sku for event in events[2:]] == [
        "CHG-001",
        "CHG-002",
        "CHG-003",
    ]
    assert buyer_message not in " ".join(
        event.explanation
        for event in events
    )


def test_base_selection_and_cross_sells_are_audited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "base-selection-audit.db",
    )
    monkeypatch.setattr(
        selection_service,
        "load_catalog",
        make_catalog,
    )
    session = create_test_session()

    selection_service.select_base_product(
        session_id=session.session_id,
        base_product_sku="CHG-001",
    )

    first_events = list_audit_events(
        session.session_id
    )

    assert [
        event.event_type
        for event in first_events
    ] == [
        "base_product_selected",
        "cross_sell_evaluated",
        "cross_sell_product_offered",
        "cross_sell_product_offered",
    ]
    assert first_events[0].actor == "buyer"
    assert first_events[0].sku == "CHG-001"
    assert [
        event.sku
        for event in first_events[2:]
    ] == ["CBL-001", "CBL-002"]

    selection_service.select_base_product(
        session_id=session.session_id,
        base_product_sku="CHG-001",
    )

    assert list_audit_events(
        session.session_id
    ) == first_events


def test_rejected_base_selection_is_audited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "rejected-selection-audit.db",
    )
    session = create_test_session()

    with pytest.raises(
        ShoppingSessionStateError,
        match="was not offered",
    ):
        selection_service.select_base_product(
            session_id=session.session_id,
            base_product_sku="CHG-999",
        )

    events = list_audit_events(session.session_id)

    assert len(events) == 1
    assert events[0].event_type == (
        "base_product_selected"
    )
    assert events[0].outcome == "rejected"
    assert events[0].reason_code == (
        "BASE_PRODUCT_NOT_OFFERED"
    )
    assert events[0].sku is None


def prepare_base_selection(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database_name: str,
):
    configure_database(
        monkeypatch,
        tmp_path,
        database_name,
    )
    monkeypatch.setattr(
        selection_service,
        "load_catalog",
        make_catalog,
    )
    session = create_test_session()

    selection_service.select_base_product(
        session_id=session.session_id,
        base_product_sku="CHG-001",
    )

    return session


def test_declined_cross_sell_and_quote_are_audited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selection(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        database_name="declined-cross-sell-audit.db",
    )

    result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="decline",
        )
    )

    events = list_audit_events(session.session_id)
    decision_event, quote_event = events[-2:]

    assert decision_event.event_type == (
        "cross_sell_decided"
    )
    assert decision_event.reason_code == (
        "BUYER_DECLINED_CROSS_SELL"
    )
    assert decision_event.sku is None
    assert decision_event.quote_id == (
        result.quote.quote_id
    )

    assert quote_event.event_type == "quote_created"
    assert quote_event.reason_code == (
        "IMMUTABLE_QUOTE_STORED"
    )
    assert quote_event.amount_paise == 100_000
    assert quote_event.quote_id == result.quote.quote_id


def test_accepted_cross_sell_retry_is_audited_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selection(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        database_name="accepted-cross-sell-audit.db",
    )

    first_result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="CBL-001",
        )
    )
    first_events = list_audit_events(
        session.session_id
    )

    repeated_result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="CBL-001",
        )
    )
    repeated_events = list_audit_events(
        session.session_id
    )

    assert repeated_result.quote == first_result.quote
    assert repeated_events == first_events

    decision_event, quote_event = first_events[-2:]

    assert decision_event.reason_code == (
        "BUYER_ACCEPTED_CROSS_SELL"
    )
    assert decision_event.sku == "CBL-001"
    assert decision_event.amount_paise == 30_000
    assert quote_event.amount_paise == 130_000


def test_unoffered_cross_sell_rejection_is_audited(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selection(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        database_name="rejected-cross-sell-audit.db",
    )

    with pytest.raises(
        ShoppingSessionStateError,
        match="was not offered",
    ):
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="CBL-999",
        )

    rejection_event = list_audit_events(
        session.session_id
    )[-1]

    assert rejection_event.event_type == (
        "cross_sell_decided"
    )
    assert rejection_event.outcome == "rejected"
    assert rejection_event.reason_code == (
        "CROSS_SELL_PRODUCT_NOT_OFFERED"
    )
    assert rejection_event.sku is None
