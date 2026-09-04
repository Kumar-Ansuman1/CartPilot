from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from backend.app.catalog import load_catalog
from backend.app.models import ProductOption, Quote
from backend.app.quote_service import create_quote
from backend.app.quote_store import (
    QuoteConflictError,
    get_stored_quote,
    save_quote_idempotently,
)
from backend.app.recommender import (
    recommend_base_products,
    recommend_cross_sell_options,
)
from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionNotFoundError,
    ShoppingSessionStateError,
    get_shopping_session,
    mark_shopping_session_expired,
    mark_shopping_session_quoted,
    record_base_product_selection,
)


class BaseSelectionError(Exception):
    pass


class CatalogVersionChangedError(
    BaseSelectionError
):
    pass


class BaseProductUnavailableError(
    BaseSelectionError
):
    pass


class SelectedProductsUnavailableError(Exception):
    pass


class BaseSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "cross_sell_decision_required"
    ]
    message: str = Field(min_length=1)

    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )
    session_expires_at: datetime

    selected_base_product: ProductOption
    cross_sell_options: list[ProductOption] = Field(
        default_factory=list,
        max_length=2,
    )

    decision_trace: list[str] = Field(
        default_factory=list
    )


class CrossSellDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["quote_ready"]
    message: str = Field(min_length=1)

    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )

    cross_sell_decision: Literal[
        "accepted",
        "declined",
    ]

    quote: Quote

    decision_trace: list[str] = Field(
        default_factory=list
    )


def _normalize_cross_sell_decision(
    *,
    decision: str,
    cross_sell_product_sku: str | None,
) -> str | None:
    if decision not in {"accept", "decline"}:
        raise ValueError(
            "Cross-sell decision must be "
            "'accept' or 'decline'."
        )

    if decision == "decline":
        if cross_sell_product_sku is not None:
            raise ValueError(
                "A declined cross-sell decision "
                "must not include a product SKU."
            )

        return None

    if (
        not isinstance(cross_sell_product_sku, str)
        or not cross_sell_product_sku.strip()
    ):
        raise ValueError(
            "An accepted cross-sell decision "
            "requires a product SKU."
        )

    return cross_sell_product_sku.strip().upper()


def _build_quote_ready_result(
    *,
    session_id: str,
    quote: Quote,
    repeated_submission: bool,
) -> CrossSellDecisionResult:
    accepted_cross_sell = (
        quote.upsell_product_sku is not None
    )

    if repeated_submission:
        decision_trace = [
            (
                "The repeated submission matched the "
                "buyer's stored cross-sell decision."
            ),
            (
                "The existing quote was returned instead "
                "of creating another quote."
            ),
            (
                "No new order or payment action "
                "was performed."
            ),
        ]
    else:
        decision_trace = [
            (
                "The buyer explicitly accepted the offered "
                "cross-sell."
                if accepted_cross_sell
                else (
                    "The buyer explicitly declined all "
                    "offered cross-sells."
                )
            ),
            (
                "The selected products were revalidated "
                "against the current trusted catalog."
            ),
            (
                "Stock, compatibility, price limits and "
                "the total budget were checked."
            ),
            (
                "The quote was stored and linked to the "
                "shopping session."
            ),
            (
                "No order or payment action was performed."
            ),
        ]

    return CrossSellDecisionResult(
        status="quote_ready",
        message=(
            "Review the quote and explicitly confirm "
            "before checkout."
        ),
        session_id=session_id,
        cross_sell_decision=(
            "accepted"
            if accepted_cross_sell
            else "declined"
        ),
        quote=quote,
        decision_trace=decision_trace,
    )


def select_base_product(
    *,
    session_id: str,
    base_product_sku: str,
) -> BaseSelectionResult:
    cleaned_base_sku = (
        base_product_sku.strip().upper()
    )

    if not cleaned_base_sku:
        raise ValueError(
            "Base-product SKU is required."
        )

    session = get_shopping_session(session_id)

    if session is None:
        raise ShoppingSessionNotFoundError(
            "Shopping session was not found."
        )

    if session.status == "expired":
        raise ShoppingSessionExpiredError(
            "Shopping session has expired."
        )

    if session.status == "quote_created":
        raise ShoppingSessionStateError(
            "This shopping session already has a quote."
        )

    if (
        session.status
        == "awaiting_cross_sell_decision"
        and session.selected_base_product_sku
        != cleaned_base_sku
    ):
        raise ShoppingSessionStateError(
            "A different base product was already selected."
        )

    if cleaned_base_sku not in session.base_product_skus:
        raise ShoppingSessionStateError(
            "Selected base product was not offered "
            "for this shopping session."
        )

    catalog = load_catalog()

    if (
        catalog.catalog_version
        != session.catalog_version
    ):
        raise CatalogVersionChangedError(
            "The catalog changed after the products "
            "were offered. Start a new shopping request."
        )

    current_base_options = recommend_base_products(
        catalog=catalog,
        request=session.request,
        limit=3,
    )

    current_products_by_sku = {
        product.sku: product
        for product in current_base_options
    }

    selected_product = current_products_by_sku.get(
        cleaned_base_sku
    )

    if selected_product is None:
        raise BaseProductUnavailableError(
            "The selected product is no longer eligible."
        )

    cross_sell_products = (
        recommend_cross_sell_options(
            catalog=catalog,
            request=session.request,
            base_product=selected_product,
            limit=2,
        )
    )

    updated_session = record_base_product_selection(
        session_id=session.session_id,
        base_product_sku=selected_product.sku,
        cross_sell_option_skus=[
            product.sku
            for product in cross_sell_products
        ],
    )

    decision_trace = [
        (
            f"Base product {selected_product.sku} "
            "was explicitly selected by the buyer."
        ),
        (
            "The selected SKU was verified against "
            "the session's offered products."
        ),
        (
            "Current price, stock, budget and "
            "compatibility were revalidated."
        ),
        (
            f"{len(cross_sell_products)} optional "
            "cross-sell choice(s) were found."
        ),
        (
            "No cross-sell was preselected and "
            "no quote was created."
        ),
    ]

    return BaseSelectionResult(
        status="cross_sell_decision_required",
        message=(
            "Choose an optional add-on or continue "
            "without one."
        ),
        session_id=updated_session.session_id,
        session_expires_at=(
            updated_session.expires_at
        ),
        selected_base_product=(
            ProductOption.from_product(
                selected_product
            )
        ),
        cross_sell_options=[
            ProductOption.from_product(product)
            for product in cross_sell_products
        ],
        decision_trace=decision_trace,
    )


def finalize_cross_sell_decision(
    *,
    session_id: str,
    decision: Literal["accept", "decline"],
    cross_sell_product_sku: str | None = None,
) -> CrossSellDecisionResult:
    selected_cross_sell_sku = (
        _normalize_cross_sell_decision(
            decision=decision,
            cross_sell_product_sku=(
                cross_sell_product_sku
            ),
        )
    )

    session = get_shopping_session(session_id)

    if session is None:
        raise ShoppingSessionNotFoundError(
            "Shopping session was not found."
        )

    if session.status == "expired":
        raise ShoppingSessionExpiredError(
            "Shopping session has expired."
        )

    if session.status == "quote_created":
        if session.quote_id is None:
            raise ShoppingSessionStateError(
                "The completed shopping session "
                "does not contain a quote ID."
            )

        stored_quote = get_stored_quote(
            session.quote_id
        )

        if stored_quote is None:
            raise ShoppingSessionStateError(
                "The shopping session's quote "
                "could not be found."
            )

        if (
            stored_quote.quote.upsell_product_sku
            != selected_cross_sell_sku
        ):
            raise ShoppingSessionStateError(
                "A different cross-sell decision "
                "was already finalized."
            )

        if stored_quote.status != "pending":
            raise ShoppingSessionStateError(
                "The existing quote has already "
                "advanced beyond review."
            )

        if (
            datetime.now(timezone.utc)
            >= stored_quote.quote.expires_at
        ):
            raise ShoppingSessionStateError(
                "The existing quote has expired."
            )

        return _build_quote_ready_result(
            session_id=session.session_id,
            quote=stored_quote.quote,
            repeated_submission=True,
        )

    if (
        datetime.now(timezone.utc)
        >= session.expires_at
    ):
        mark_shopping_session_expired(
            session.session_id
        )

        raise ShoppingSessionExpiredError(
            "Shopping session has expired."
        )

    if (
        session.status
        != "awaiting_cross_sell_decision"
    ):
        raise ShoppingSessionStateError(
            "A base product must be selected before "
            "the cross-sell decision."
        )

    if session.selected_base_product_sku is None:
        raise ShoppingSessionStateError(
            "The shopping session has no selected "
            "base product."
        )

    if (
        selected_cross_sell_sku is not None
        and selected_cross_sell_sku
        not in session.cross_sell_option_skus
    ):
        raise ShoppingSessionStateError(
            "Selected cross-sell product was not offered "
            "for this shopping session."
        )

    catalog = load_catalog()

    if (
        catalog.catalog_version
        != session.catalog_version
    ):
        raise CatalogVersionChangedError(
            "The catalog changed after the products "
            "were offered. Start a new shopping request."
        )

    try:
        quote = create_quote(
            catalog=catalog,
            request=session.request,
            base_product_sku=(
                session.selected_base_product_sku
            ),
            upsell_product_sku=(
                selected_cross_sell_sku
            ),
            session_id=session.session_id,
        )
    except ValueError as exc:
        raise SelectedProductsUnavailableError(
            str(exc)
        ) from exc

    try:
        stored_quote = save_quote_idempotently(
            quote
        )
    except QuoteConflictError as exc:
        raise ShoppingSessionStateError(
            str(exc)
        ) from exc

    if stored_quote.status != "pending":
        raise ShoppingSessionStateError(
            "The existing quote has already "
            "advanced beyond review."
        )

    updated_session = mark_shopping_session_quoted(
        session_id=session.session_id,
        quote_id=stored_quote.quote.quote_id,
    )

    return _build_quote_ready_result(
        session_id=updated_session.session_id,
        quote=stored_quote.quote,
        repeated_submission=False,
    )