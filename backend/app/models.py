from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=3)
    name: str = Field(min_length=2)
    description: str = Field(min_length=5)
    category: str = Field(min_length=2)
    price_paise: int = Field(gt=0)
    stock: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)
    compatibility_tags: list[str] = Field(default_factory=list)
    cross_sell_skus: list[str] = Field(default_factory=list)
    active: bool = True


class ShoppingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, max_length=500)
    budget_paise: int = Field(gt=0, le=500_000)
    currency: Literal["INR"] = "INR"

    allowed_categories: list[str] = Field(default_factory=list)
    compatibility_tags: list[str] = Field(default_factory=list)

    max_items: int = Field(default=3, ge=1, le=5)
    max_upsell_percentage: int = Field(default=20, ge=0, le=30)

    # A request can never turn off checkout confirmation.
    confirmation_required: Literal[True] = True

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        cleaned_value = value.strip()

        if not cleaned_value:
            raise ValueError("Shopping query cannot be empty.")

        return cleaned_value

    @field_validator(
        "allowed_categories",
        "compatibility_tags",
    )
    @classmethod
    def normalize_list(cls, values: list[str]) -> list[str]:
        normalized_values: list[str] = []

        for value in values:
            cleaned_value = value.strip().lower()

            if (
                cleaned_value
                and cleaned_value not in normalized_values
            ):
                normalized_values.append(cleaned_value)

        return normalized_values

class Quote(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    quote_id: str = Field(min_length=5)
    catalog_version: str = Field(min_length=1)
    currency: Literal["INR"] = "INR"

    base_product_sku: str = Field(min_length=3)
    base_price_paise: int = Field(gt=0)

    upsell_product_sku: str | None = Field(
        default=None,
        min_length=3,
    )
    upsell_price_paise: int = Field(default=0, ge=0)

    total_paise: int = Field(gt=0)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_quote(self) -> "Quote":
        calculated_total = (
            self.base_price_paise
            + self.upsell_price_paise
        )

        if self.total_paise != calculated_total:
            raise ValueError(
                "Quote total does not match its product prices."
            )

        has_upsell_sku = self.upsell_product_sku is not None
        has_upsell_price = self.upsell_price_paise > 0

        if has_upsell_sku != has_upsell_price:
            raise ValueError(
                "Upsell SKU and price must be provided together."
            )

        if (
            self.created_at.tzinfo is None
            or self.expires_at.tzinfo is None
        ):
            raise ValueError(
                "Quote timestamps must include timezone information."
            )

        if self.expires_at <= self.created_at:
            raise ValueError(
                "Quote expiry must be after its creation time."
            )

        return self

ProductCategory = Literal[
    "chargers",
    "cables",
    "power-banks",
    "stands",
    "cases",
    "screen-protectors",
    "audio",
    "mounts",
]

CompatibilityTag = Literal[
    "usb-c",
    "android",
    "iphone",
    "iphone-15",
    "iphone-15-and-newer",
    "iphone-14-and-older",
    "lightning",
    "tablet",
    "laptop",
    "bluetooth",
    "universal",
]


class ExtractedShoppingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_query: str = Field(
        min_length=3,
        max_length=200,
        description=(
            "Concise product-search keywords derived from "
            "the buyer's message."
        ),
    )

    budget_rupees: int | None = Field(
        description=(
            "The buyer's stated budget in whole rupees, "
            "or null when no budget was provided."
        ),
    )

    requested_categories: list[ProductCategory] = Field(
        description=(
            "Product categories explicitly requested "
            "or clearly implied by the buyer."
        ),
    )

    compatibility_tags: list[CompatibilityTag] = Field(
        description=(
            "Known device and connector compatibility "
            "requirements extracted from the message."
        ),
    )

    needs_clarification: bool = Field(
        description=(
            "Whether essential information is missing."
        ),
    )

    clarification_question: str | None = Field(
        description=(
            "One concise question for the buyer, or null "
            "when clarification is unnecessary."
        ),
    )

    @model_validator(mode="after")
    def validate_clarification(
        self,
    ) -> "ExtractedShoppingIntent":
        if self.budget_rupees is None:
            if not self.needs_clarification:
                raise ValueError(
                    "A missing budget requires clarification."
                )

        if (
            self.needs_clarification
            and not self.clarification_question
        ):
            raise ValueError(
                "A clarification question is required."
            )

        if (
            not self.needs_clarification
            and self.clarification_question is not None
        ):
            raise ValueError(
                "Clarification question must be null when "
                "clarification is unnecessary."
            )

        return self