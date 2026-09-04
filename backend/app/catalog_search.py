import re

from backend.app.catalog import Catalog
from backend.app.models import Product


WORD_PATTERN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Convert text into normalized searchable words."""
    return set(WORD_PATTERN.findall(text.lower()))


def _tokenize_list(values: list[str]) -> set[str]:
    tokens: set[str] = set()

    for value in values:
        tokens.update(_tokenize(value))

    return tokens


def search_catalog(
    catalog: Catalog,
    query: str,
    max_price_paise: int | None = None,
    category: str | None = None,
    compatibility_tags: list[str] | None = None,
    limit: int = 5,
) -> list[Product]:
    if limit < 1:
        raise ValueError("Search limit must be at least 1.")

    if max_price_paise is not None and max_price_paise <= 0:
        raise ValueError("Maximum price must be greater than zero.")

    query_tokens = _tokenize(query)
    requested_category = category.strip().lower() if category else None

    requested_compatibility = {
        tag.strip().lower()
        for tag in (compatibility_tags or [])
        if tag.strip()
    }

    scored_products: list[tuple[int, Product]] = []

    for product in catalog.available_products():
        if (
            max_price_paise is not None
            and product.price_paise > max_price_paise
        ):
            continue

        if (
            requested_category is not None
            and product.category.lower() != requested_category
        ):
            continue

        product_compatibility = {
            tag.lower() for tag in product.compatibility_tags
        }

        if requested_compatibility:
            is_universal = "universal" in product_compatibility
            supports_requested_tags = requested_compatibility.issubset(
                product_compatibility
            )

            if not is_universal and not supports_requested_tags:
                continue

        name_tokens = _tokenize(product.name)
        tag_tokens = _tokenize_list(product.tags)
        category_tokens = _tokenize(product.category)
        description_tokens = _tokenize(product.description)
        compatibility_tokens = _tokenize_list(
            product.compatibility_tags
        )

        score = (
            5 * len(query_tokens & name_tokens)
            + 4 * len(query_tokens & tag_tokens)
            + 3 * len(query_tokens & category_tokens)
            + 2 * len(query_tokens & compatibility_tokens)
            + len(query_tokens & description_tokens)
        )

        if query_tokens and score == 0:
            continue

        scored_products.append((score, product))

    scored_products.sort(
        key=lambda item: (
            -item[0],
            item[1].price_paise,
            item[1].sku,
        )
    )

    return [
        product
        for _, product in scored_products[:limit]
    ]