# CartPilot

CartPilot is a buyer-controlled agentic-commerce system for the fictional electronics merchant **VoltCart**. It combines LLM-based intent understanding and delegated product selection with a deterministic commerce core that owns catalog eligibility, prices, stock checks, quotes, mandate enforcement, checkout, payment verification, webhook recovery, and audit history.

> **Current status:** the complete local flow works with Razorpay Test Mode. Normal shopping and delegated AI Buyer modes are available from the same frontend, the delegated cross-sell flow is buyer-controlled, and the latest validation completed with **230 backend tests passed**, frontend ESLint passed, and the production Vite build passed. Production-style webhook recovery will be validated after deployment.

## What makes CartPilot different

CartPilot is not just a shopping chatbot. It is designed around **bounded delegation**:

- the buyer defines an immutable purchase mandate,
- deterministic code filters what the AI is allowed to consider,
- the AI chooses only from that eligible set,
- an optional add-on may be recommended by the AI,
- the buyer explicitly accepts or declines that add-on,
- deterministic code creates the final immutable quote,
- the buyer still confirms checkout,
- payment verification remains server controlled and auditable.

The core rule is:

> **AI may influence choice. Deterministic code owns commerce authority. The buyer still gates payment.**

## Frontend modes

The frontend now exposes two clear shopping modes through a global mode switcher:

```text
Normal
You choose
```

```text
AI Buyer
AI chooses
```

Routes:

- `/` — normal buyer-controlled shopping
- `/ai-buyer` — delegated AI Buyer mode

The shared frontend uses **Manrope** for the site-wide type system.

## 1. Normal shopping mode

```text
Buyer request
    -> AI intent extraction
    -> deterministic catalog filtering
    -> buyer selects base product
    -> buyer accepts/declines add-on
    -> deterministic revalidation
    -> immutable quote
    -> buyer confirms checkout
    -> Razorpay
```

In this mode the AI understands the request, but the buyer chooses the product and any optional add-on.

## 2. Delegated AI Buyer mode

```text
Buyer creates immutable mandate
    -> mandate authority reserved
    -> deterministic catalog filtering
    -> AI chooses one eligible base product
    -> deterministic cross-sell filtering
    -> AI may recommend one eligible companion
    -> buyer checks/unchecks the recommended add-on
    -> deterministic revalidation
    -> final immutable quote is created
    -> buyer confirms checkout
    -> Razorpay order
    -> payment verification / webhook recovery
```

The important distinction is that the AI recommendation happens **before** the final immutable quote. The add-on checkbox is a real buyer decision, not just frontend presentation.

### Buyer-controlled AI add-on

If the AI recommends a companion product, the UI shows:

```text
[ ] Include this AI-recommended add-on
```

Unchecked:

```text
Base product only
```

Checked:

```text
Base product + AI-recommended eligible add-on
```

The selected checkbox value is sent to the backend during delegated checkout confirmation. The backend revalidates the selected products and only then creates the final quote and Razorpay order.

The mandate's `max_cross_sell_percentage` remains the **maximum authority boundary**. It does not force the buyer to purchase the add-on.

## Buyer purchase mandates

A purchase mandate is an immutable buyer-approved authorization containing:

- budget in paise,
- fixed INR currency,
- allowed product categories,
- required compatibility tags,
- maximum cross-sell percentage,
- buyer goal,
- expiry time,
- mandatory checkout confirmation.

Example:

```text
Budget: <= Rs 2,000
Category: chargers
Compatibility: Android + USB-C
Cross-sell: <= 20% of approved budget
Goal: choose a compact everyday charger
Checkout confirmation: required
```

The mandate itself is never mutated. Usage is tracked separately in the append-only execution ledger.

## Mandate execution ledger

The execution ledger prevents one mandate from being reused concurrently or spent multiple times.

```text
reserved
   -> session_bound
   -> quote_bound
   -> consumed

or, before consumption:

reserved / quote_bound -> released
```

During AI selection, the execution is reserved and session-bound. The final quote is bound only after the buyer finalizes the optional add-on choice.

## Safety boundary

| The AI may | The AI may not |
|---|---|
| Interpret a buyer request | Invent a catalog SKU |
| Select a base product from deterministic eligible options | Set or modify prices |
| Recommend one deterministic eligible companion | Override stock or compatibility rules |
| Explain its recommendation | Modify an approved mandate |
| Return strict structured selections | Force the buyer to accept an add-on |
| Influence purchase choice | Create a Razorpay order without buyer confirmation |
| | Verify, forge, or declare payment success |

The LLM is never the authoritative source for price, inventory, quote total, order identity, or payment state.

## Commerce Flight Recorder

CartPilot records persistent audit events for important commerce actions, including:

- intent extraction,
- catalog search,
- product offers,
- AI and buyer selections,
- cross-sell evaluation and decision,
- mandate creation and policy checks,
- quote creation,
- checkout confirmation,
- Razorpay order creation,
- payment verification,
- payment rejection,
- webhook reconciliation.

Buyer-visible timelines are available for both shopping sessions and purchase mandates.

## Payment recovery

CartPilot supports two verification paths:

```text
Browser callback
    -> server-side signature verification
```

and, if the browser callback is lost:

```text
Signed Razorpay webhook
    -> backend reconciliation
    -> frontend payment-status polling
```

Webhook event IDs and payload hashes are persisted to prevent unsafe replay or conflicting reuse.

## Tech stack

- **Backend:** Python, FastAPI, Pydantic, LangChain Groq
- **Commerce logic:** deterministic Python search, ranking, recommendations, mandate policy, quoting, checkout and payment validation
- **Storage:** SQLite
- **Catalog:** versioned local JSON
- **Payments:** Razorpay Test Mode, Orders API, signature verification, signed webhooks
- **Frontend:** React 19, Vite 8, Lucide React
- **Typography:** Manrope
- **Testing:** Pytest, FastAPI TestClient
- **CI:** GitHub Actions for backend tests and frontend lint/build checks

## Run locally

### Prerequisites

- Git
- Python 3.11+
- Node.js `20.19+` or `22.12+`
- Groq API key
- Razorpay Test Mode keys
- Razorpay webhook secret for webhook testing

### Backend setup

```bash
git clone https://github.com/Kumar-Ansuman1/CartPilot.git
cd CartPilot
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Create `.env` from `.env.example`:

```dotenv
GROQ_API_KEY=your_groq_key
GROQ_MODEL=openai/gpt-oss-20b

RAZORPAY_KEY_ID=rzp_test_your_key_id
RAZORPAY_KEY_SECRET=your_test_key_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret
```

Never commit `.env`.

### Start the full backend

Use the delegated-enabled entrypoint so both normal and AI Buyer APIs are exposed:

```bash
python -m uvicorn backend.app.main_delegated:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

### Start the frontend

```bash
cd frontend
npm ci
npm run dev
```

Open:

```text
Normal mode:  http://localhost:5173/
AI Buyer:     http://localhost:5173/ai-buyer
```

The mode switcher lets you move between both experiences from the UI.

## Delegated API flow

### Create mandate

```http
POST /api/mandates
```

### Ask the AI Buyer to choose

```http
POST /api/delegated-shop
```

This stage returns the AI-selected base product and, when available, an AI-recommended eligible companion. **No final quote or Razorpay order is created yet.**

The same planning capability is also exposed through:

```http
POST /api/agent/purchase-plan
```

### Buyer finalizes optional add-on and checkout

```http
POST /api/delegated-checkout/confirm
```

Example request shape:

```json
{
  "execution_id": "execution_...",
  "include_cross_sell": true,
  "confirmed": true
}
```

At this point CartPilot:

1. validates the mandate and execution,
2. accepts or declines the AI-recommended cross-sell according to the buyer's checkbox,
3. revalidates product eligibility,
4. creates the final immutable quote,
5. binds that quote to the execution ledger,
6. creates the Razorpay order,
7. consumes the delegated mandate authority.

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
| `POST` | `/api/delegated-shop` | Reserve mandate authority and run constrained AI product selection |
| `POST` | `/api/delegated-checkout/confirm` | Finalize buyer add-on choice, create quote/order, and consume authority |
| `POST` | `/api/agent/purchase-plan` | Restricted external-agent purchase-plan capability |
| `GET` | `/api/agent/executions/{execution_id}` | Read delegated execution state |
| `GET` | `/api/agent/capabilities` | Describe allowed and prohibited agent capabilities |

### Payments

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/payment/verify` | Verify browser callback signature and store payment |
| `GET` | `/api/payment/status/{quote_id}` | Read payment/recovery status |
| `POST` | `/api/payment/webhook` | Process a signed Razorpay webhook |

## Verification

Backend:

```bash
python -m pytest backend/test -q
```

Latest CI result:

```text
230 passed, 1 warning
```

Frontend:

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
```

Latest checks:

```text
ESLint            passed
Production build  passed
```

Razorpay webhook recovery is implemented and automated-test verified. Final production-style webhook recovery remains a post-deployment validation task.

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
│   ├── README.md
│   └── src/
│       ├── App.jsx
│       ├── AiBuyer.jsx
│       ├── ModeSwitcher.jsx
│       ├── api.js
│       └── razorpay.js
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
- The AI Buyer depends on the configured Groq model being available.
- External-agent integration currently uses a restricted HTTP capability surface rather than a dedicated MCP server.
- Final deployed webhook-recovery validation is still pending.

See [ARCHITECTURE.md](ARCHITECTURE.md) for trust boundaries, invariants, state transitions, and production gaps. See [DELEGATED_BUYER.md](DELEGATED_BUYER.md) for the delegated-authority design in detail.
