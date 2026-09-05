# CartPilot

CartPilot is a buyer-controlled, agentic-commerce demo for the fictional electronics merchant **VoltCart**. A language model turns a natural-language request into validated shopping intent; deterministic Python code owns catalog lookup, eligibility, prices, stock checks, quotes, order creation, and payment verification.

> **Current status:** the complete flow works locally with Razorpay Test Mode. This is a learning/demo system, not a production checkout service. Use Test Mode credentials only—no real money is required or intended.

## What works today

- Natural-language shopping intent extraction through Groq structured output.
- Deterministic filtering and ranking against a trusted local catalog.
- Up to three eligible base-product choices, with a non-binding “best match” hint.
- Explicit buyer selection of the base product.
- Up to two optional, catalog-approved cross-sells; nothing is preselected.
- Explicit acceptance or rejection of the add-on step.
- Five-minute server-stored quotes using integer paise.
- Explicit checkout confirmation before Razorpay order creation.
- Razorpay Checkout in Test Mode.
- Server-side Razorpay signature verification before a payment is stored as verified.
- SQLite persistence for shopping sessions, quotes, order IDs, and verified payments.

## Safety boundary

| The AI may | The AI may not |
|---|---|
| Interpret the buyer’s words | Invent or select a SKU |
| Extract a budget, category, and compatibility requirements | Set a catalog price or stock level |
| Ask for missing details | Create or alter a quote |
| Produce a short search query | Create an order or verify a payment |

The model’s output is schema-validated. It can influence the search constraints, but all purchasable items and money-related values must come from deterministic code and the trusted catalog. The buyer still chooses the product and explicitly confirms checkout.

## Buyer flow

1. Enter a request such as `I need a USB-C charger for Android under ₹2,000`.
2. Groq extracts validated intent. Missing budget or compatibility details stop the flow for clarification.
3. The backend converts rupees to paise, searches the catalog, and returns at most three eligible products.
4. The buyer selects one offered base product.
5. The backend revalidates it and may offer at most two approved companion products.
6. The buyer accepts one offered add-on or explicitly continues without one.
7. The backend revalidates the chosen terms and stores a five-minute quote.
8. The buyer reviews the total and clicks **Confirm & Pay**.
9. The backend creates one Razorpay order from the stored quote amount.
10. The frontend opens Razorpay Checkout.
11. The backend verifies the returned Razorpay signature and only then records the payment as verified.

Cross-sells can be from a different category—for example, charger → cable—but only when the base product explicitly lists that SKU as an approved companion. The add-on must also be active, in stock, compatible, within the remaining total budget, and no more than 20% of the buyer’s original budget.

## Stack

- Backend: Python, FastAPI, Pydantic, LangChain Groq
- Commerce logic: deterministic Python search, ranking, recommendation, and validation
- Storage: SQLite
- Payments: Razorpay Checkout and Orders API in Test Mode
- Frontend: React 19, Vite 8, Lucide React
- Tests: Pytest and FastAPI TestClient

## Run locally

The setup is intentionally short and uses committed dependency manifests and demo catalog data. The target is a working clone in under 15 minutes once API credentials are available. A clean-machine setup time has **not yet been formally measured**, so that is a target rather than a benchmark.

### 1. Prerequisites

- Git
- Python 3.11 or newer
- Node.js `20.19+` or `22.12+` (required by the locked Vite version)
- A [Groq API key](https://console.groq.com/keys)
- Razorpay **Test Mode** API keys from the Razorpay dashboard

Do not use Razorpay live-mode credentials. The application does not yet enforce the `rzp_test_` key prefix in code.

### 2. Clone and prepare the backend

```bash
git clone https://github.com/Kumar-Ansuman1/CartPilot.git
cd CartPilot
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS or Linux
source .venv/bin/activate
```

Install the backend dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Copy the environment template:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS or Linux
cp .env.example .env
```

Fill in `.env`:

```dotenv
GROQ_API_KEY=your_groq_key
GROQ_MODEL=openai/gpt-oss-20b

RAZORPAY_KEY_ID=rzp_test_your_key_id
RAZORPAY_KEY_SECRET=your_test_key_secret
RAZORPAY_WEBHOOK_SECRET=
```

`RAZORPAY_WEBHOOK_SECRET` is reserved for future webhook support and may remain empty. Never commit `.env`; it is ignored by Git.

Start the API from the repository root:

```bash
python -m uvicorn backend.app.main:app --reload
```

Check [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) and browse the interactive API docs at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 3. Start the frontend

Open a second terminal in the repository root:

```bash
cd frontend
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The frontend uses `http://127.0.0.1:8000` by default. Set `VITE_API_BASE_URL` before starting Vite if the API runs elsewhere.

### 4. Try a Test Mode purchase

Use a request that includes both a product and budget, for example:

```text
I need a USB-C charger for Android under ₹2,000
```

Select a base product, accept or decline an optional add-on, review the quote, and use Razorpay’s published Test Mode payment details in Checkout. A successful Checkout response is not trusted by itself; CartPilot sends it to the backend for signature verification.

## Tests and build checks

Backend tests mock external service boundaries and should not make real Groq or Razorpay calls:

```bash
python -m pytest backend/test -q
```

Frontend checks:

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
```

Current local verification snapshot:

- 109 backend tests passed.
- The frontend production build passed.
- A complete Razorpay Test Mode payment was manually completed and verified by the backend.
- There is no CI workflow yet, and the setup time has not yet been measured on a fresh machine.

Note: `pytest.ini` currently points to `backend/tests`, while the committed suite is in `backend/test`. Use the explicit test command above until that configuration is corrected.

## API surface

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Service health check |
| `POST` | `/api/shop` | Extract intent and return base-product choices |
| `POST` | `/api/shop/select-base` | Record the buyer’s offered base-product selection |
| `POST` | `/api/shop/select-cross-sell` | Accept one offered add-on or explicitly decline |
| `POST` | `/api/checkout/confirm` | Create or return the linked Razorpay order |
| `POST` | `/api/payment/verify` | Verify the Razorpay signature and store the payment |

## Local data

- `backend/data/products.json` is the versioned demo catalog. Version `2.0.0` contains 33 products across eight categories, including deliberate inactive and out-of-stock cases.
- `backend/data/cartpilot.db` is created automatically on first use. No seed script is necessary because the catalog is JSON and SQLite tables are created by the stores.
- Set `CARTPILOT_DB_PATH` to use a different SQLite file, which is useful for isolated tests.
- Database files, virtual environments, frontend builds, dependencies, and `.env` are ignored by Git.

## Project layout

```text
CartPilot/
├── backend/
│   ├── app/                 # API, intent adapter, deterministic core, stores
│   ├── data/products.json   # trusted VoltCart demo catalog
│   ├── test/                # backend tests
│   └── requirements.txt
├── frontend/
│   ├── src/                 # React UI, API client, Razorpay helper
│   ├── package.json
│   └── package-lock.json
├── .env.example
├── pytest.ini
└── ARCHITECTURE.md
```

## Known limits

- Intended for a single local API process and Razorpay Test Mode only.
- No user authentication, authorization, session ownership, rate limiting, or production deployment configuration.
- The checkout lock is process-local; it does not prevent duplicate remote orders across multiple workers or process crashes.
- Inventory is checked but not reserved or decremented.
- Razorpay webhooks and reconciliation are not implemented.
- The Test Mode key policy is documented but not enforced at startup.
- The Groq call is required for the interactive shopping flow; there is no offline runtime fallback.
- SQLite and the static JSON catalog are suitable for this demo, not distributed commerce.
- Frontend shopping state is held in memory, so a page refresh does not restore the active flow.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the trust boundaries, state transitions, invariants, and production gaps.
