# CartPilot

CartPilot is a buyer-controlled agentic-commerce demo for the fictional electronics merchant **VoltCart**. It combines LLM-based intent understanding and delegated product selection with a deterministic commerce core that owns catalog eligibility, prices, stock checks, quotes, mandate enforcement, checkout, payment verification, webhook recovery, and audit history.

> **Current status:** the complete local flow works with Razorpay Test Mode. The delegated AI-buyer flow has also been manually verified. Production-style webhook recovery will be validated after deployment. CartPilot is still a learning/buildathon system, not a production checkout platform.

## What CartPilot can do

- Natural-language shopping intent extraction through Groq structured output.
- Deterministic filtering and ranking against a trusted local catalog.
- Up to three eligible base-product choices in the normal shopping flow.
- Explicit buyer selection and optional cross-sell handling in the normal flow.
- Immutable five-minute server-stored quotes using integer paise.
- Explicit checkout confirmation before Razorpay order creation.
- Razorpay Checkout in Test Mode.
- Server-side Razorpay signature verification before a payment is stored as verified.
- Signed Razorpay webhook recovery for lost browser callbacks.
- Read-only payment-status polling for frontend recovery.
- Persistent Commerce Flight Recorder audit events for buyer, AI, deterministic-core, and Razorpay actions.
- Buyer-visible audit timelines for shopping sessions and purchase mandates.
- Immutable buyer-approved purchase mandates.
- An append-only mandate execution ledger that prevents the same authority from funding multiple purchases.
- A delegated AI buyer that may select a product only from deterministic, mandate-approved SKU sets.
- A deliberately restricted external-agent API that cannot mutate mandates or bypass buyer checkout confirmation.

## Two shopping modes

### 1. Buyer-controlled shopping

```text
Buyer request
    -> AI intent extraction
    -> deterministic catalog filtering
    -> buyer selects base product
    -> buyer accepts/declines add-on
    -> immutable quote
    -> buyer confirms checkout
    -> Razorpay
```

In this mode the AI interprets language but the human buyer chooses the product.

### 2. Delegated AI buyer

```text
Buyer creates immutable mandate
    -> mandate authority reserved
    -> deterministic catalog filtering
    -> AI chooses only from eligible SKUs
    -> deterministic revalidation
    -> mandate-bound shopping session
    -> immutable quote
    -> buyer confirms checkout
    -> Razorpay
    -> mandate authority consumed
```

In delegated mode the AI may make the product decision, but it does **not** receive unrestricted commerce or payment authority.

## Safety boundary

| The AI may | The AI may not |
|---|---|
| Interpret a buyer request | Invent a catalog SKU |
| Extract structured shopping intent | Set or modify catalog prices |
| Rank/select from a deterministic eligible SKU set in delegated mode | Override stock or compatibility checks |
| Explain why it chose an eligible product | Modify a buyer-approved mandate |
| Propose an optional eligible cross-sell | Create a Razorpay order without buyer confirmation |
| Return a strict structured purchase plan | Verify, forge, or declare a payment successful |

The important rule is:

> **AI may influence choice. Deterministic code owns commerce authority. The buyer still gates payment.**

The model never supplies the authoritative price, inventory value, quote total, order identity, or payment result. Those values come from trusted server-side state.

## Buyer purchase mandates

A purchase mandate is an immutable buyer-approved authorization containing fields such as:

- budget in paise
- fixed INR currency
- allowed product categories
- required compatibility tags
- maximum cross-sell percentage
- buyer goal
- expiry time
- mandatory checkout confirmation

Example intent:

```text
Budget: <= Rs 2,000
Category: chargers
Compatibility: Android + USB-C
Cross-sell: <= 20%
Goal: choose a compact everyday charger
Checkout confirmation: required
```

The mandate itself is never mutated. Usage is tracked separately through an append-only execution ledger.

## Mandate execution lifecycle

```text
reserved
   -> session_bound
   -> quote_bound
   -> consumed

or, before consumption:

reserved / quote_bound -> released
```

Only one active delegated execution may use a mandate. A consumed mandate cannot be reused. A failed execution may be released when it is safe to do so. Once a live quote exists, CartPilot intentionally keeps the reservation so the same mandate cannot fund another quote at the same time.

## Payment recovery and audit trail

CartPilot records buyer-visible audit events for important commerce transitions, including intent extraction, catalog search, product offers and selections, quote creation, checkout confirmation, order creation, payment verification, payment rejection, mandate authorization, and webhook reconciliation.

For payment recovery:

1. Checkout first attempts the normal browser callback path.
2. The backend verifies the Razorpay signature against the server-stored order.
3. If the browser callback is lost, a signed Razorpay `order.paid` webhook can reconcile the payment.
4. The frontend can poll the read-only payment-status API until the payment is verified or the quote expires.
5. Webhook event IDs and payload hashes are persisted to prevent unsafe replay/conflicting reuse.

## Stack

- Backend: Python, FastAPI, Pydantic, LangChain Groq
- Commerce logic: deterministic Python search, ranking, recommendation, mandate enforcement, quote and payment validation
- Storage: SQLite
- Catalog: versioned local JSON
- Payments: Razorpay Checkout, Orders API, signature verification, signed webhooks
- Frontend: React 19, Vite 8, Lucide React
- Tests: Pytest and FastAPI TestClient
- CI: GitHub Actions workflow for the delegated-buyer branch, plus manual dispatch

## Run locally

### 1. Prerequisites

- Git
- Python 3.11 or newer
- Node.js `20.19+` or `22.12+`
- A Groq API key
- Razorpay **Test Mode** API keys
- Razorpay webhook secret when testing webhook reconciliation

### 2. Clone and prepare the backend

```bash
git clone https://github.com/Kumar-Ansuman1/CartPilot.git
cd CartPilot
python -m venv .venv
```

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

or on macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Copy `.env.example` to `.env` and configure:

```dotenv
GROQ_API_KEY=your_groq_key
GROQ_MODEL=openai/gpt-oss-20b

RAZORPAY_KEY_ID=rzp_test_your_key_id
RAZORPAY_KEY_SECRET=your_test_key_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret
```

Never commit `.env`.

### 3. Start the API

For the original buyer-controlled flow:

```bash
python -m uvicorn backend.app.main:app --reload
```

For the full API including delegated commerce:

```bash
python -m uvicorn backend.app.main_delegated:app --reload
```

Then open:

```text
http://127.0.0.1:8000/docs
```

### 4. Start the frontend

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`.

## Delegated-buyer quick test

Create a mandate:

```http
POST /api/mandates
```

Then request a delegated purchase plan:

```http
POST /api/delegated-shop
```

or through the deliberately restricted external-agent surface:

```http
POST /api/agent/purchase-plan
```

A successful delegated result stops at:

```text
purchase_ready_for_confirmation
```

No Razorpay order exists yet.

The buyer must then explicitly call:

```http
POST /api/delegated-checkout/confirm
```

Only this confirmation may create/reuse the Razorpay order and consume the mandate authority.

## Tests and build checks

Backend:

```bash
python -m pytest backend/test -q
```

Frontend:

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
```

Current verification status:

- The full backend suite passed in GitHub Actions after the delegated-buyer work was added.
- The delegated purchase flow was manually tested end to end through quote creation and buyer confirmation.
- Frontend ESLint and the production Vite build passed during the earlier commerce-audit/payment-recovery phase.
- Razorpay webhook recovery is implemented and automated-test verified; final production-style recovery testing remains a post-deployment check.

## API surface

### Core commerce

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Service health check |
| `POST` | `/api/shop` | Extract intent and return buyer-selectable products |
| `GET` | `/api/shop/{session_id}/audit` | Read shopping-session audit history |
| `POST` | `/api/shop/select-base` | Record buyer base-product selection |
| `POST` | `/api/shop/select-cross-sell` | Accept or decline an offered add-on |
| `POST` | `/api/checkout/confirm` | Buyer-confirm a normal-flow quote and create/reuse Razorpay order |

### Mandates and delegated commerce

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/mandates` | Create an immutable purchase mandate |
| `GET` | `/api/mandates/{mandate_id}` | Read a mandate |
| `GET` | `/api/mandates/{mandate_id}/audit` | Read mandate audit history |
| `POST` | `/api/delegated-shop` | Run mandate-bound AI product selection and quote creation |
| `POST` | `/api/delegated-checkout/confirm` | Buyer-confirm delegated quote and consume mandate authority |
| `POST` | `/api/agent/purchase-plan` | Restricted external-agent purchase-plan capability |
| `GET` | `/api/agent/executions/{execution_id}` | Read mandate execution state |
| `GET` | `/api/agent/capabilities` | Describe allowed and prohibited external-agent capabilities |

### Payments

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/payment/verify` | Verify browser callback signature and store payment |
| `GET` | `/api/payment/status/{quote_id}` | Read payment/recovery status |
| `POST` | `/api/payment/webhook` | Process a signed Razorpay webhook |

## Project layout

```text
CartPilot/
├── backend/
│   ├── app/
│   │   ├── commerce_agent.py
│   │   ├── delegated_buyer.py
│   │   ├── delegated_api.py
│   │   ├── mandate_execution_ledger.py
│   │   ├── purchase_mandate_service.py
│   │   ├── audit_events.py
│   │   ├── payment_webhook.py
│   │   ├── payment_status.py
│   │   ├── main.py
│   │   └── main_delegated.py
│   ├── data/products.json
│   ├── test/
│   └── requirements.txt
├── frontend/
│   └── src/
├── .github/workflows/
├── ARCHITECTURE.md
├── DELEGATED_BUYER.md
└── README.md
```

## Known limits

- Demo/Test Mode system; not production-ready.
- No user authentication, account ownership checks, or production authorization model.
- SQLite and a static JSON catalog are not suitable for distributed commerce.
- Inventory is checked but not reserved/decremented as part of a distributed inventory transaction.
- Multi-worker idempotency and process-crash recovery are not fully production hardened.
- No refund/dispute lifecycle.
- The delegated AI buyer still depends on the Groq model being available.
- Final deployed webhook-recovery testing is still pending.
- External-agent integration currently uses a restricted HTTP capability surface; it is not yet packaged as a dedicated MCP server.

See [ARCHITECTURE.md](ARCHITECTURE.md) for trust boundaries, invariants, state transitions, and production gaps. See [DELEGATED_BUYER.md](DELEGATED_BUYER.md) for the delegated-authority design in isolation.
