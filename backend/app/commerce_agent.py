from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from backend.app.catalog import load_catalog
from backend.app.intent_extractor import (
    extract_shopping_intent,
)
from backend.app.models import (
    ExtractedShoppingIntent,
    ProductOption,
)
from backend.app.recommender import (
    recommend_base_products,
)
from backend.app.request_builder import (
    build_shopping_request,
)
from backend.app.shopping_session_store import (
    create_shopping_session,
)


class CommerceAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "clarification_required",
        "no_match",
        "base_selection_required",
    ]
    message: str = Field(min_length=1)
    intent: ExtractedShoppingIntent

    session_id: str | None = Field(
        default=None,
        pattern=r"^session_[0-9a-f]{32}$",
    )
    session_expires_at: datetime | None = None

    base_product_options: list[ProductOption] = Field(
        default_factory=list,
        max_length=3,
    )
    recommended_base_product_sku: str | None = None

    decision_trace: list[str] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_result(
        self,
    ) -> "CommerceAgentResult":
        if self.status == "base_selection_required":
            if self.session_id is None:
                raise ValueError(
                    "Base selection requires a session ID."
                )

            if self.session_expires_at is None:
                raise ValueError(
                    "Base selection requires a session expiry."
                )

            if (
                self.session_expires_at.tzinfo
                is None
            ):
                raise ValueError(
                    "Session expiry must include timezone information."
                )

            if not self.base_product_options:
                raise ValueError(
                    "Base selection requires product options."
                )

            offered_skus = {
                product.sku
                for product in self.base_product_options
            }

            if (
                self.recommended_base_product_sku
                not in offered_skus
            ):
                raise ValueError(
                    "Recommended product must be one "
                    "of the offered products."
                )

        else:
            if (
                self.session_id is not None
                or self.session_expires_at is not None
                or self.base_product_options
                or self.recommended_base_product_sku
                is not None
            ):
                raise ValueError(
                    "Only a base-selection result may "
                    "contain shopping-session options."
                )

        return self


def run_commerce_agent(
    buyer_message: str,
) -> CommerceAgentResult:
    intent = extract_shopping_intent(
        buyer_message
    )

    if intent.needs_clarification:
        return CommerceAgentResult(
            status="clarification_required",
            message=(
                intent.clarification_question
                or "Please provide more shopping details."
            ),
            intent=intent,
            decision_trace=[
                "The request stopped before catalog search.",
                "No shopping session, quote or payment was created.",
            ],
        )

    request = build_shopping_request(intent)
    catalog = load_catalog()

    decision_trace = [
        (
            "Buyer budget was bounded to "
            f"{request.budget_paise} paise."
        ),
        (
            "Product prices and stock were loaded "
            "from the trusted catalog."
        ),
    ]

    base_products = recommend_base_products(
        catalog=catalog,
        request=request,
        limit=3,
    )

    if not base_products:
        decision_trace.append(
            "No in-stock product satisfied "
            "the request constraints."
        )

        return CommerceAgentResult(
            status="no_match",
            message=(
                "I could not find an in-stock product "
                "matching your budget and compatibility "
                "requirements."
            ),
            intent=intent,
            decision_trace=decision_trace,
        )

    session = create_shopping_session(
        request=request,
        catalog_version=catalog.catalog_version,
        base_product_skus=[
            product.sku
            for product in base_products
        ],
    )

    recommended_product = base_products[0]

    decision_trace.append(
        (
            f"{len(base_products)} eligible base-product "
            "option(s) were selected by deterministic ranking."
        )
    )
    decision_trace.append(
        (
            f"{recommended_product.sku} is the highest-ranked "
            "option, but the buyer must make the final selection."
        )
    )
    decision_trace.append(
        "No cross-sell, quote or payment action was created."
    )

    return CommerceAgentResult(
        status="base_selection_required",
        message=(
            "Choose one of the eligible products "
            "before continuing."
        ),
        intent=intent,
        session_id=session.session_id,
        session_expires_at=session.expires_at,
        base_product_options=[
            ProductOption.from_product(product)
            for product in base_products
        ],
        recommended_base_product_sku=(
            recommended_product.sku
        ),
        decision_trace=decision_trace,
    )