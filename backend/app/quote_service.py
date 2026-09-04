from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.app.catalog import Catalog
from backend.app.models import Product, Quote, ShoppingRequest


def _get_available_product(
    catalog: Catalog,
    sku: str,
) -> Product:
    product = catalog.get_product(sku)

    if product is None:
        raise ValueError(
            f"Product '{sku}' does not exist in the catalogue."
        )

    if not product.active or product.stock <= 0:
        raise ValueError(
            f"Product '{sku}' is currently unavailable."
        )

    return product


def _validate_product_permissions(
    product: Product,
    request: ShoppingRequest,
) -> None:
    if (
        request.allowed_categories
        and product.category.lower()
        not in set(request.allowed_categories)
    ):
        raise ValueError(
            f"Product '{product.sku}' belongs to a category "
            "that the buyer did not permit."
        )

    if not request.compatibility_tags:
        return

    product_compatibility = {
        tag.lower() for tag in product.compatibility_tags
    }
    required_compatibility = set(
        request.compatibility_tags
    )

    is_compatible = (
        "universal" in product_compatibility
        or required_compatibility.issubset(
            product_compatibility
        )
    )

    if not is_compatible:
        raise ValueError(
            f"Product '{product.sku}' does not satisfy the "
            "buyer's compatibility requirements."
        )


def create_quote(
    catalog: Catalog,
    request: ShoppingRequest,
    base_product_sku: str,
    upsell_product_sku: str | None = None,
    validity_minutes: int = 5,
) -> Quote:
    if not 1 <= validity_minutes <= 15:
        raise ValueError(
            "Quote validity must be between 1 and 15 minutes."
        )

    base_product = _get_available_product(
        catalog,
        base_product_sku,
    )
    _validate_product_permissions(base_product, request)

    if base_product.price_paise > request.budget_paise:
        raise ValueError(
            "Base product exceeds the buyer's budget."
        )

    upsell_product: Product | None = None

    if upsell_product_sku is not None:
        if request.max_items < 2:
            raise ValueError(
                "The buyer's item limit does not permit an upsell."
            )

        if request.max_upsell_percentage == 0:
            raise ValueError(
                "The buyer did not permit any upsell."
            )

        if upsell_product_sku == base_product.sku:
            raise ValueError(
                "The base product cannot also be the cross-sell."
            )

        if upsell_product_sku not in base_product.cross_sell_skus:
            raise ValueError(
                f"Product '{upsell_product_sku}' is not an "
                f"approved cross-sell for '{base_product.sku}'."
            )

        upsell_product = _get_available_product(
            catalog,
            upsell_product_sku,
        )
        _validate_product_permissions(
            upsell_product,
            request,
        )

        upsell_limit = (
            request.budget_paise
            * request.max_upsell_percentage
            // 100
        )

        if upsell_product.price_paise > upsell_limit:
            raise ValueError(
                "Cross-sell product exceeds the buyer's "
                "upsell limit."
            )

    upsell_price_paise = (
        upsell_product.price_paise
        if upsell_product is not None
        else 0
    )

    total_paise = (
        base_product.price_paise
        + upsell_price_paise
    )

    if total_paise > request.budget_paise:
        raise ValueError(
            "Quote total exceeds the buyer's budget."
        )

    created_at = datetime.now(timezone.utc)

    return Quote(
        quote_id=f"quote_{uuid4().hex}",
        catalog_version=catalog.catalog_version,
        currency=catalog.currency,
        base_product_sku=base_product.sku,
        base_price_paise=base_product.price_paise,
        upsell_product_sku=(
            upsell_product.sku
            if upsell_product is not None
            else None
        ),
        upsell_price_paise=upsell_price_paise,
        total_paise=total_paise,
        created_at=created_at,
        expires_at=created_at + timedelta(
            minutes=validity_minutes
        ),
    )