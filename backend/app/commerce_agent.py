from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.catalog import load_catalog
from backend.app.intent_extractor import extract_shopping_intent
from backend.app.models import ExtractedShoppingIntent, Product, Quote
from backend.app.quote_service import create_quote
from backend.app.recommender import (
    recommend_base_product,
    recommend_cross_sell,
)
from backend.app.request_builder import build_shopping_request

from backend.app.quote_store import save_quote


class CommerceAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "clarification_required",
        "no_match",
        "quote_ready",
    ]
    message: str
    intent: ExtractedShoppingIntent
    base_product: Product | None = None
    upsell_product: Product | None = None
    quote: Quote | None = None
    decision_trace: list[str] = Field(default_factory=list)


def run_commerce_agent(
    buyer_message: str,
) -> CommerceAgentResult:
    intent = extract_shopping_intent(buyer_message)

    if intent.needs_clarification:
        return CommerceAgentResult(
            status="clarification_required",
            message=intent.clarification_question
            or "Please provide more shopping details.",
            intent=intent,
            decision_trace=[
                "The request was stopped before catalog search.",
                "No quote or payment action was created.",
            ],
        )

    request = build_shopping_request(intent)
    catalog = load_catalog()

    decision_trace = [
        f"Buyer budget bounded to {request.budget_paise} paise.",
        "Product prices and stock were loaded from the trusted catalog.",
    ]

    base_product = recommend_base_product(catalog, request)

    if base_product is None:
        decision_trace.append(
            "No in-stock product satisfied the request constraints."
        )

        return CommerceAgentResult(
            status="no_match",
            message=(
                "I could not find an in-stock product matching your "
                "budget and compatibility requirements."
            ),
            intent=intent,
            decision_trace=decision_trace,
        )

    decision_trace.append(
        f"Base product {base_product.sku} was selected by deterministic ranking."
    )

    upsell_product = recommend_cross_sell(
        catalog,
        request,
        base_product,
    )

    if upsell_product is None:
        decision_trace.append(
            "No eligible cross-sell satisfied the configured limits."
        )
    else:
        decision_trace.append(
            f"Cross-sell {upsell_product.sku} satisfied the budget "
            "and upsell limits."
        )

    quote = create_quote(
        catalog=catalog,
        request=request,
        base_product_sku=base_product.sku,
        upsell_product_sku=(
            upsell_product.sku if upsell_product else None
        ),
    )
    save_quote(quote)

    decision_trace.append(
        f"Quote {quote.quote_id} was created from trusted catalog prices."
    )
    decision_trace.append(
        "Payment was not initiated; explicit buyer confirmation is required."
    )

    return CommerceAgentResult(
        status="quote_ready",
        message=(
            f"I found {base_product.name}. "
            "Review the quote before confirming checkout."
        ),
        intent=intent,
        base_product=base_product,
        upsell_product=upsell_product,
        quote=quote,
        decision_trace=decision_trace,
    )