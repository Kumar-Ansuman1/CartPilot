# CartPilot Delegated Buyer

This feature branch adds a mandate-bound AI buyer without giving the model direct payment authority.

## Runtime entrypoint

Run the delegated-commerce API with:

```bash
python -m uvicorn backend.app.main_delegated:app --reload
```

`main_delegated` imports the existing CartPilot API unchanged and adds the delegated-commerce router. The original `backend.app.main:app` remains available for the existing interactive buyer flow.

## Delegated flow

1. Buyer creates an immutable purchase mandate through `POST /api/mandates`.
2. An internal or external agent calls `POST /api/delegated-shop` or `POST /api/agent/purchase-plan` with the mandate ID and task.
3. CartPilot reserves that mandate in an append-only execution ledger before any AI planning occurs.
4. The deterministic commerce core filters the trusted catalog by mandate budget, allowed categories, compatibility, stock, and product state.
5. The AI planner receives only those eligible SKU options and returns a strict `DelegatedBuyerPlan`.
6. CartPilot rejects any SKU outside the supplied eligible set and re-runs deterministic mandate authorization on the selected products.
7. A shopping session and immutable quote are created and bound to the mandate execution.
8. The flow stops with `purchase_ready_for_confirmation`; no Razorpay order has been created.
9. The buyer explicitly calls `POST /api/delegated-checkout/confirm` with `confirmed: true`.
10. Only then does the existing checkout service create or reuse the Razorpay order. The mandate execution becomes consumed so the same authority cannot fund another purchase.

## Execution states

The mandate itself remains immutable. Usage is represented by append-only events:

```text
reserved -> session_bound -> quote_bound -> consumed
                              \
                               -> released (only before consumption)
```

A consumed mandate cannot be reserved again. A failed pre-quote execution can be released and retried. Once a live quote is bound, the reservation is intentionally retained even if a later audit write fails, preventing the mandate from being reused while the quote remains confirmable.

## External-agent boundary

The external-agent API deliberately exposes only:

- create a mandate-bound purchase plan
- read execution state
- read the existing mandate and audit APIs

It does not expose mandate mutation, catalog price mutation, unrestricted Razorpay order creation, or payment verification authority. Buyer checkout confirmation remains mandatory.

## Validation

The branch includes focused tests for:

- append-only execution lifecycle
- single-use mandate consumption
- safe release and retry
- delegated AI selection restricted to deterministic eligible SKUs
- quote-to-mandate execution binding
- blocking a second execution while the first quote is still active

The branch-scoped workflow `.github/workflows/delegated-buyer-ci.yml` runs the full backend test suite on each push to `feature/buyer-purchase-mandate`.
