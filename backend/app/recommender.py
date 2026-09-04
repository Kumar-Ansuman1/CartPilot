from backend.app.catalog import Catalog
from backend.app.catalog_search import search_catalog
from backend.app.models import Product, ShoppingRequest


def recommend_base_product(
    catalog: Catalog,
    request: ShoppingRequest,
) -> Product | None:
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
            if product.category.lower() in allowed_categories
        ]

    if not candidates:
        return None

    return candidates[0]


def _supports_compatibility(
    product: Product,
    requested_tags: list[str],
) -> bool:
    if not requested_tags:
        return True

    product_tags = {
        tag.lower() for tag in product.compatibility_tags
    }
    required_tags = set(requested_tags)

    return (
        "universal" in product_tags
        or required_tags.issubset(product_tags)
    )


def recommend_cross_sell(
    catalog: Catalog,
    request: ShoppingRequest,
    base_product: Product,
) -> Product | None:
    if request.max_items < 2:
        return None

    if request.max_upsell_percentage == 0:
        return None

    trusted_base_product = catalog.get_product(base_product.sku)

    if trusted_base_product is None:
        raise ValueError(
            f"Base product '{base_product.sku}' is not in the catalogue."
        )

    if (
        not trusted_base_product.active
        or trusted_base_product.stock <= 0
    ):
        raise ValueError(
            f"Base product '{base_product.sku}' is unavailable."
        )

    if trusted_base_product.price_paise > request.budget_paise:
        raise ValueError("Base product exceeds the buyer's budget.")

    remaining_budget = (
        request.budget_paise
        - trusted_base_product.price_paise
    )

    upsell_limit = (
        request.budget_paise
        * request.max_upsell_percentage
        // 100
    )

    maximum_upsell_price = min(
        remaining_budget,
        upsell_limit,
    )

    if maximum_upsell_price <= 0:
        return None

    allowed_categories = set(request.allowed_categories)
    eligible_products: list[Product] = []

    for cross_sell_sku in trusted_base_product.cross_sell_skus:
        product = catalog.get_product(cross_sell_sku)

        if product is None:
            continue

        if not product.active or product.stock <= 0:
            continue

        if product.price_paise > maximum_upsell_price:
            continue

        if (
            allowed_categories
            and product.category.lower() not in allowed_categories
        ):
            continue

        if not _supports_compatibility(
            product,
            request.compatibility_tags,
        ):
            continue

        eligible_products.append(product)

    if not eligible_products:
        return None

    eligible_products.sort(
        key=lambda product: (
            -product.price_paise,
            product.sku,
        )
    )

    return eligible_products[0]