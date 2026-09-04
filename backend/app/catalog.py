import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from backend.app.models import Product


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "products.json"
)


@dataclass
class Catalog:
    merchant_id: str
    merchant_name: str
    catalog_version: str
    currency: str
    products: dict[str, Product]

    def get_product(self, sku: str) -> Product | None:
        """Find a product using a case-insensitive SKU."""
        return self.products.get(sku.strip().upper())

    def available_products(self) -> list[Product]:
        """Return products that can currently be recommended."""
        return [
            product
            for product in self.products.values()
            if product.active and product.stock > 0
        ]


def _get_required_text(data: dict, field_name: str) -> str:
    value = data.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Catalogue field '{field_name}' must be a non-empty string."
        )

    return value.strip()


def load_catalog(path: Path | None = None) -> Catalog:
    catalog_path = path or DEFAULT_CATALOG_PATH

    if not catalog_path.exists():
        raise ValueError(f"Catalogue file not found: {catalog_path}")

    try:
        raw_data = json.loads(
            catalog_path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Catalogue contains invalid JSON: {exc}"
        ) from exc

    if not isinstance(raw_data, dict):
        raise ValueError("Catalogue root must be a JSON object.")

    raw_products = raw_data.get("products")

    if not isinstance(raw_products, list):
        raise ValueError(
            "Catalogue field 'products' must be a list."
        )

    products_by_sku: dict[str, Product] = {}

    for index, raw_product in enumerate(raw_products):
        try:
            product = Product.model_validate(raw_product)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid product at index {index}: {exc}"
            ) from exc

        normalized_sku = product.sku.strip().upper()

        if product.sku != normalized_sku:
            raise ValueError(
                f"Product SKU '{product.sku}' must be uppercase "
                "and contain no surrounding spaces."
            )

        if normalized_sku in products_by_sku:
            raise ValueError(
                f"Duplicate product SKU found: {normalized_sku}"
            )

        products_by_sku[normalized_sku] = product

    for product in products_by_sku.values():
        for cross_sell_sku in product.cross_sell_skus:
            if cross_sell_sku == product.sku:
                raise ValueError(
                    f"Product '{product.sku}' cannot cross-sell itself."
                )

            if cross_sell_sku not in products_by_sku:
                raise ValueError(
                    f"Product '{product.sku}' references unknown "
                    f"cross-sell SKU '{cross_sell_sku}'."
                )

    return Catalog(
        merchant_id=_get_required_text(raw_data, "merchant_id"),
        merchant_name=_get_required_text(raw_data, "merchant_name"),
        catalog_version=_get_required_text(raw_data, "catalog_version"),
        currency=_get_required_text(raw_data, "currency"),
        products=products_by_sku,
    )