# CartPilot Delegated Buyer

CartPilot now includes a mandate-bound AI buyer on `main` without giving the model direct payment authority.

## Runtime entrypoint

Run the full delegated-commerce API with:

```bash
python -m uvicorn backend.app.main_delegated:app --reload
```

`main_delegated.py` imports the existing CartPilot API and adds the delegated-commerce router. The original `backend.app.main:app` remains available for the buyer-controlled shopping flow.

## Concept

The original CartPilot AI acted as a shopping assistant: it interpreted the buyer request, while the human selected the product.

The delegated buyer changes that responsibility:

```text
Buyer creates mandate
    -> deterministic core filters the catalog
    -> AI chooses only from eligible SKUs
    -> deterministic core revalidates the AI choice
    -> immutable quote is created
    -> buyer confirms checkout
    -> Razorpay order is created
```

The AI therefore receives bounded **decision authority**, not unrestricted **commerce authority**.

## Delegated flow

1. Buyer creates an immutable purchase mandate through `POST /api/mandates`.
2. An internal or external agent calls `POST /api/delegated-shop` or `POST /api/agent/purchase-plan` with the mandate ID and optional task.
3. CartPilot reserves that mandate in the append-only execution ledger before AI planning occurs.
4. The deterministic core filters the trusted catalog by mandate budget, allowed categories, compatibility, stock, and product state.
5. The AI planner receives only those eligible product options and returns a strict `DelegatedBuyerPlan`.
6. CartPilot rejects any SKU outside the supplied eligible set and re-runs deterministic mandate authorization on the selected products.
7. A shopping session and immutable quote are created and bound to the mandate execution.
8. The flow stops with `purchase_ready_for_confirmation`; no Razorpay order exists yet.
9. The buyer explicitly calls `POST /api/delegated-checkout/confirm` with `confirmed: true`.
10. Only then does the existing checkout service create or reuse the Razorpay order and consume the mandate execution authority.

## Purchase mandate

A mandate is immutable buyer-approved authority. Important fields include:

- budget in paise
- INR currency
- allowed categories
- required compatibility tags
- maximum cross-sell percentage
- optional buyer goal
- creation/expiry timestamps
- mandatory checkout confirmation

There is no mandate-update path. The AI cannot raise the budget or broaden its own permissions.

## Execution states

Mandate usage is represented separately using append-only execution events:

```text
reserved
   -> session_bound
   -> quote_bound
   -> consumed
```

An active execution can be released before consumption when it is safe to do so.

Important properties:

- only one active execution may use a mandate,
- a consumed mandate cannot be reserved again,
- a failed pre-purchase execution may be released and retried,
- once a live quote is bound, the reservation is intentionally retained so a second purchase cannot be created from the same mandate.

This prevents the classic static-budget problem where several individually valid purchases could exceed the buyer's total approved authority.

## AI planner boundary

The model does not receive unrestricted catalog access as authority.

The deterministic core first creates bounded candidate sets. The AI sees only:

- eligible base products,
- eligible cross-sells for each base product,
- buyer goal/task,
- immutable mandate constraints.

The strict plan contains:

- `base_product_sku`
- optional `cross_sell_product_sku`
- `reason`
- `confidence`

After the model returns a plan, CartPilot checks that the SKUs are still members of the exact eligible sets and revalidates them against the mandate and trusted catalog.

## External-agent boundary

The restricted external-agent HTTP surface exposes only high-level capabilities:

- create a mandate-bound purchase plan,
- read execution state,
- read existing mandates through the core API,
- read audit history through the core API.

It deliberately does not expose:

- mandate mutation,
- catalog price mutation,
- unrestricted Razorpay order creation,
- payment verification or forged payment state.

Buyer checkout confirmation remains mandatory.

Current external-agent support is an HTTP capability boundary; CartPilot is not yet packaged as a dedicated MCP server.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/mandates` | Create immutable buyer authority |
| `GET` | `/api/mandates/{mandate_id}` | Read mandate |
| `GET` | `/api/mandates/{mandate_id}/audit` | Read mandate audit timeline |
| `POST` | `/api/delegated-shop` | Run delegated AI selection and quote creation |
| `POST` | `/api/agent/purchase-plan` | Same safe planning capability for an external agent |
| `GET` | `/api/agent/executions/{execution_id}` | Read delegated execution state |
| `GET` | `/api/agent/capabilities` | Describe allowed/prohibited agent capabilities |
| `POST` | `/api/delegated-checkout/confirm` | Explicitly buyer-confirm quote and consume mandate authority |

## Relationship to payment recovery

The delegated buyer does not replace CartPilot's payment safeguards. After buyer confirmation, the existing payment architecture still applies:

- server-stored immutable quote,
- Razorpay order created from trusted server values,
- server-side browser callback signature verification,
- signed `order.paid` webhook recovery,
- read-only payment-status polling,
- persistent Commerce Flight Recorder audit events.

The delegated AI never declares that a payment succeeded.

## Validation

Implemented test coverage includes:

- append-only execution lifecycle,
- single-use mandate consumption,
- safe release/retry behavior,
- blocking a second active execution,
- delegated AI selection restricted to deterministic eligible SKUs,
- quote-to-mandate execution binding,
- existing purchase-mandate policy validation.

The full backend suite passed in GitHub Actions after delegated-buyer integration. The delegated flow has also been manually tested. Final production-style Razorpay webhook recovery validation is intentionally deferred until deployment.

## Core safety statement

> CartPilot allows an AI to make a purchase decision on behalf of a buyer without allowing that AI to redefine the buyer's authority or independently authorize payment.

That distinction is the central design goal of Delegated Purchase Mode.
