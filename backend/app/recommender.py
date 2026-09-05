from backend.app.catalog import Catalog
from backend.app.catalog_search import search_catalog
from backend.app.models import Product, ShoppingRequest


def recommend_base_products(
    catalog: Catalog,
    request: ShoppingRequest,
    limit: int = 3,
) -> list[Product]:
    """Return ranked base-product options for buyer selection."""
    if not 1 <= limit <= 5:
        raise ValueError(
            "Base-product option limit must be between 1 and 5."
        )

    if not catalog.products:
        return []

    candidates = search_catalog(
        catalog=catalog,
        query=request.query,
        max_price_paise=request.budget_paise,
        compatibility_tags=request.compatibility_tags,
        limit=len(catalog.products),
    )

    if request.allowed_categories:
        allowed_categories = set(request.allowed_categories)

        candidates = [
            product
            for product in candidates
            if product.category.strip().lower()
            in allowed_categories
        ]

    return candidates[:limit]


def recommend_base_product(
    catalog: Catalog,
    request: ShoppingRequest,
) -> Product | None:
    """
    Preserve the existing single-product behaviour until the
    shopping workflow is migrated to buyer selection.
    """
    candidates = recommend_base_products(
        catalog=catalog,
        request=request,
        limit=1,
    )

    if not candidates:
        return None

    return candidates[0]


def _supports_compatibility(
    product: Product,
    requested_tags: list[str],
) -> bool:
    required_tags = {
        tag.strip().lower()
        for tag in requested_tags
        if tag.strip()
    }

    if not required_tags:
        return True

    product_tags = {
        tag.strip().lower()
        for tag in product.compatibility_tags
        if tag.strip()
    }

    return (
        "universal" in product_tags
        or required_tags.issubset(product_tags)
    )


def _collect_cross_sell_candidates(
    catalog: Catalog,
    request: ShoppingRequest,
    base_product: Product,
    *,
    enforce_base_categories: bool,
) -> list[Product]:
    if (
        request.max_items < 2
        or request.max_upsell_percentage == 0
    ):
        return []

    trusted_base_product = catalog.get_product(
        base_product.sku
    )

    if (
        trusted_base_product is None
        or not trusted_base_product.active
        or trusted_base_product.stock <= 0
        or trusted_base_product.price_paise
        > request.budget_paise
    ):
        return []

    remaining_budget = (
        request.budget_paise
        - trusted_base_product.price_paise
    )

    percentage_limit = (
        request.budget_paise
        * request.max_upsell_percentage
        // 100
    )

    cross_sell_price_limit = min(
        remaining_budget,
        percentage_limit,
    )

    if cross_sell_price_limit <= 0:
        return []

    allowed_categories = set(
        request.allowed_categories
    )

    eligible_products: list[Product] = []

    for cross_sell_sku in (
        trusted_base_product.cross_sell_skus
    ):
        product = catalog.get_product(cross_sell_sku)

        if (
            product is None
            or not product.active
            or product.stock <= 0
        ):
            continue

        if product.price_paise > cross_sell_price_limit:
            continue

        # This category restriction is retained only for the
        # legacy single-cross-sell workflow.
        if (
            enforce_base_categories
            and allowed_categories
            and product.category.strip().lower()
            not in allowed_categories
        ):
            continue

        if not _supports_compatibility(
            product,
            request.compatibility_tags,
        ):
            continue

        eligible_products.append(product)

    return eligible_products


def recommend_cross_sell_options(
    catalog: Catalog,
    request: ShoppingRequest,
    base_product: Product,
    limit: int = 2,
) -> list[Product]:
    """
    Return optional companion products for buyer selection.

    Base-product category restrictions are intentionally not
    applied because an approved companion may belong to another
    category, such as charger to cable.
    """
    if not 1 <= limit <= 2:
        raise ValueError(
            "Cross-sell option limit must be between 1 and 2."
        )

    eligible_products = _collect_cross_sell_candidates(
        catalog=catalog,
        request=request,
        base_product=base_product,
        enforce_base_categories=False,
    )

    eligible_products.sort(
        key=lambda product: (
            product.price_paise,
            product.sku,
        )
    )

    return eligible_products[:limit]


def recommend_cross_sell(
    catalog: Catalog,
    request: ShoppingRequest,
    base_product: Product,
) -> Product | None:
    """
    Preserve the existing behaviour until the old workflow is
    replaced by explicit buyer selection.
    """
    eligible_products = _collect_cross_sell_candidates(
        catalog=catalog,
        request=request,
        base_product=base_product,
        enforce_base_categories=True,
    )

    if not eligible_products:
        return None

    eligible_products.sort(
        key=lambda product: (
            -product.price_paise,
            product.sku,
        )
    )

    return eligible_products[0]