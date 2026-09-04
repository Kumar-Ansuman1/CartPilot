from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from backend.app.catalog import load_catalog
from backend.app.models import ProductOption
from backend.app.recommender import (
    recommend_base_products,
    recommend_cross_sell_options,
)
from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionNotFoundError,
    ShoppingSessionStateError,
    get_shopping_session,
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