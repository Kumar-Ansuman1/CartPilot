# CartPilot Frontend

This directory contains the React/Vite frontend for CartPilot.

The interface exposes two shopping modes from one application:

```text
/            -> Normal Shopping
/ai-buyer    -> Delegated AI Buyer
```

A shared mode switcher appears in the UI so users can move between both experiences without manually changing the URL.

## Frontend stack

- React 19
- Vite 8
- Lucide React
- Manrope typography
- Razorpay Checkout integration
- Fetch-based API client in `src/api.js`

## Shopping modes

### Normal Shopping

In normal mode the AI extracts intent, but the buyer chooses the base product and optional cross-sell.

```text
Buyer request
    -> AI intent extraction
    -> deterministic product options
    -> buyer selects base product
    -> buyer accepts/declines add-on
    -> immutable quote
    -> buyer confirms checkout
```

Main files:

- `src/App.jsx`
- `src/App.css`

### Delegated AI Buyer

In AI Buyer mode the buyer first approves an immutable purchase mandate. CartPilot then lets the AI choose only from deterministic eligible products.

```text
Buyer mandate
    -> deterministic eligible base products
    -> AI chooses base product
    -> deterministic eligible companions
    -> AI recommends one companion when available
    -> buyer checks/unchecks the add-on
    -> backend creates final immutable quote
    -> buyer confirms payment
```

The add-on is **not automatically purchased**. The AI recommendation is shown with:

```text
[ ] Include this AI-recommended add-on
```

The checkbox is unchecked by default.

If the buyer leaves it unchecked, the final quote contains only the base product. If checked, the backend revalidates the companion and creates the quote with both items.

Main files:

- `src/AiBuyer.jsx`
- `src/AiBuyer.css`

## Mode switcher

`src/ModeSwitcher.jsx` provides the shared selector:

```text
Normal
You choose

AI Buyer
AI chooses
```

The active mode is visually highlighted.

## Typography

Global typography is configured in `src/index.css` and uses **Manrope** with system fallbacks.

Form controls inherit the same typeface so both normal and AI Buyer experiences remain visually consistent.

## API integration

`src/api.js` communicates with the FastAPI backend.

Important delegated endpoints used by the frontend include:

- `POST /api/mandates`
- `GET /api/mandates/{mandate_id}/audit`
- `POST /api/delegated-shop`
- `POST /api/delegated-checkout/confirm`
- `GET /api/agent/executions/{execution_id}`
- `POST /api/payment/verify`
- `GET /api/payment/status/{quote_id}`

The delegated checkout request sends the buyer's final add-on decision:

```json
{
  "execution_id": "execution_...",
  "include_cross_sell": true,
  "confirmed": true
}
```

The backend then returns both the final immutable quote and the Razorpay checkout order.

## Razorpay flow

`src/razorpay.js` loads Razorpay Checkout and returns the browser payment callback values to the React flow.

The frontend never treats the browser callback itself as proof of payment. It sends the callback values to the backend for signature verification.

If browser verification is interrupted, the frontend polls the payment-status endpoint so a verified Razorpay webhook can recover the transaction.

## Local development

From the `frontend` directory:

```bash
npm ci
npm run dev
```

Open:

```text
http://localhost:5173/
```

AI Buyer directly:

```text
http://localhost:5173/ai-buyer
```

The backend should be running with the delegated-enabled entrypoint:

```bash
python -m uvicorn backend.app.main_delegated:app --reload
```

By default the frontend expects:

```text
http://127.0.0.1:8000
```

To use another backend URL, copy `frontend/.env.example` to `frontend/.env` and configure `VITE_API_BASE_URL`.

## Quality checks

Run ESLint:

```bash
npm run lint
```

Build the production bundle:

```bash
npm run build
```

The latest AI Buyer frontend validation passed both ESLint and the production Vite build.

## Key frontend files

```text
src/
├── App.jsx
├── App.css
├── AiBuyer.jsx
├── AiBuyer.css
├── ModeSwitcher.jsx
├── ModeSwitcher.css
├── api.js
├── index.css
├── main.jsx
└── razorpay.js
```

## Safety notes

The frontend is intentionally not the source of truth for commerce state.

It may display buyer choices, AI recommendations, execution state, quotes, and payment progress, but trusted values such as price, eligibility, final quote amount, Razorpay order amount, and payment verification are controlled by the backend.
