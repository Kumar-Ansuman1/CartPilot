from backend.app.models import (
    ExtractedShoppingIntent,
    ShoppingRequest,
)


def build_shopping_request(
    intent: ExtractedShoppingIntent,
) -> ShoppingRequest:
    if intent.needs_clarification:
        raise ValueError(
            "Cannot build shopping request while clarification is required."
        )

    if intent.budget_rupees is None:
        raise ValueError(
            "Cannot build shopping request without a budget."
        )

    return ShoppingRequest(
        query=intent.search_query,
        budget_paise=intent.budget_rupees * 100,
        allowed_categories=list(intent.requested_categories),
        compatibility_tags=list(intent.compatibility_tags),
        confirmation_required=True,
    )