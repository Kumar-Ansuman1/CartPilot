import { useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  LockKeyhole,
  PackageCheck,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import {
  confirmDelegatedCheckout,
  createPurchaseMandate,
  getDelegatedExecution,
  getMandateAudit,
  getPaymentStatus,
  runDelegatedShop,
  verifyPayment,
} from "./api";
import { openRazorpayCheckout } from "./razorpay";
import "./AiBuyer.css";

const CATEGORIES = [
  "chargers",
  "cables",
  "power-banks",
  "stands",
  "cases",
  "screen-protectors",
  "audio",
  "mounts",
];

const COMPATIBILITY = [
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
];

function formatMoney(amountPaise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
  }).format(amountPaise / 100);
}

function toggleValue(values, value) {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function AiBuyer() {
  const [budgetRupees, setBudgetRupees] = useState("2000");
  const [allowedCategories, setAllowedCategories] = useState([
    "chargers",
  ]);
  const [requiredCompatibility, setRequiredCompatibility] = useState([
    "usb-c",
    "android",
  ]);
  const [maxCrossSellPercentage, setMaxCrossSellPercentage] = useState(0);
  const [expiresInMinutes, setExpiresInMinutes] = useState(30);
  const [buyerGoal, setBuyerGoal] = useState(
    "Choose a compact everyday charger with good value."
  );
  const [task, setTask] = useState(
    "Choose the best compact charger for everyday use."
  );

  const [mandate, setMandate] = useState(null);
  const [purchase, setPurchase] = useState(null);
  const [execution, setExecution] = useState(null);
  const [audit, setAudit] = useState(null);
  const [status, setStatus] = useState("idle");
  const [paymentStatus, setPaymentStatus] = useState("idle");
  const [error, setError] = useState("");

  const busy = [
    "creating_mandate",
    "planning",
    "opening_checkout",
  ].includes(status);
  const crossSellEnabled = Number(maxCrossSellPercentage) > 0;

  const canCreateMandate = useMemo(() => {
    const budget = Number(budgetRupees);
    return (
      Number.isInteger(budget) &&
      budget > 0 &&
      budget <= 5000 &&
      allowedCategories.length > 0 &&
      buyerGoal.trim().length >= 3
    );
  }, [budgetRupees, allowedCategories, buyerGoal]);

  async function refreshExecution(executionId) {
    if (!executionId) return;
    try {
      setExecution(await getDelegatedExecution(executionId));
    } catch {
      // The execution panel is supplemental; do not replace the main flow.
    }
  }

  async function refreshAudit(mandateId) {
    if (!mandateId) return;
    try {
      setAudit(await getMandateAudit(mandateId));
    } catch {
      // The audit panel is supplemental; do not replace the main flow.
    }
  }

  async function handleCreateMandate(event) {
    event.preventDefault();
    if (!canCreateMandate || busy) return;

    setStatus("creating_mandate");
    setError("");
    setPurchase(null);
    setExecution(null);
    setAudit(null);
    setPaymentStatus("idle");

    try {
      const created = await createPurchaseMandate({
        budgetPaise: Number(budgetRupees) * 100,
        allowedCategories,
        requiredCompatibility,
        maxCrossSellPercentage: Number(maxCrossSellPercentage),
        expiresInMinutes: Number(expiresInMinutes),
        buyerGoal: buyerGoal.trim(),
      });
      setMandate(created);
      setStatus("mandate_ready");
      void refreshAudit(created.mandate_id);
    } catch (requestError) {
      setError(requestError.message);
      setStatus("idle");
    }
  }

  async function handleDelegatePurchase() {
    if (!mandate || busy || task.trim().length > 500) return;

    setStatus("planning");
    setError("");
    setPurchase(null);
    setExecution(null);
    setPaymentStatus("idle");

    try {
      const result = await runDelegatedShop(
        mandate.mandate_id,
        task.trim()
      );
      setPurchase(result);
      setStatus("quote_ready");
      void refreshExecution(result.execution_id);
      void refreshAudit(result.mandate_id);
    } catch (requestError) {
      setError(requestError.message);
      setStatus("mandate_ready");
      void refreshAudit(mandate.mandate_id);
    }
  }

  async function pollPayment(quoteId) {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2500));
      try {
        const result = await getPaymentStatus(quoteId);
        if (result.status === "verified") {
          setPaymentStatus(
            result.verification_source === "webhook"
              ? "recovered"
              : "verified"
          );
          setStatus("completed");
          return;
        }
      } catch {
        // Keep polling; webhook recovery may arrive after a temporary failure.
      }
    }
    setPaymentStatus("pending");
  }

  async function handleConfirmAndPay() {
    if (!purchase || busy || paymentStatus !== "idle") return;

    setStatus("opening_checkout");
    setPaymentStatus("creating_order");
    setError("");

    try {
      const checkoutOrder = await confirmDelegatedCheckout(
        purchase.quote.quote_id
      );
      await refreshExecution(purchase.execution_id);
      await refreshAudit(purchase.mandate_id);

      setPaymentStatus("checkout_open");
      const razorpayResponse = await openRazorpayCheckout({
        checkoutOrder,
        productName: purchase.base_product.name,
      });

      setPaymentStatus("verifying");

      try {
        const verified = await verifyPayment(
          purchase.quote.quote_id,
          razorpayResponse
        );
        setPaymentStatus(
          verified.verification_source === "webhook"
            ? "recovered"
            : "verified"
        );
        setStatus("completed");
      } catch {
        setPaymentStatus("pending");
        setStatus("quote_ready");
        void pollPayment(purchase.quote.quote_id);
      }

      void refreshExecution(purchase.execution_id);
      void refreshAudit(purchase.mandate_id);
    } catch (checkoutError) {
      setError(checkoutError.message);
      setPaymentStatus("idle");
      setStatus("quote_ready");
    }
  }

  function resetFlow() {
    setMandate(null);
    setPurchase(null);
    setExecution(null);
    setAudit(null);
    setError("");
    setStatus("idle");
    setPaymentStatus("idle");
  }

  return (
    <main className="ai-buyer-shell">
      <header className="ai-buyer-topbar">
        <a href="/" className="back-link">
          <ChevronLeft size={17} />
          Shopping assistant
        </a>

        <span className="test-chip">
          <span /> Razorpay Test Mode
        </span>
      </header>

      <section className="ai-buyer-hero">
        <span className="ai-buyer-label">
          <Sparkles size={15} /> Delegated purchase mode
        </span>
        <h1>
          Approve the rules. <span>Let the AI choose.</span>
        </h1>
        <p>
          The AI buyer may select only from products that deterministic code
          has already approved under your immutable purchase mandate. Payment
          still requires your confirmation.
        </p>
      </section>

      <section className="ai-buyer-grid">
        <div className="mandate-card">
          <div className="section-heading">
            <div>
              <span className="eyebrow">1. Buyer authority</span>
              <h2>Create purchase mandate</h2>
              <p>These limits become immutable once approved.</p>
            </div>
            <ShieldCheck size={22} />
          </div>

          <form onSubmit={handleCreateMandate} className="mandate-form">
            <label>
              Budget (rupees)
              <input
                type="number"
                min="1"
                max="5000"
                value={budgetRupees}
                onChange={(event) => setBudgetRupees(event.target.value)}
                disabled={Boolean(mandate) || busy}
              />
            </label>

            <label>
              Buyer goal
              <textarea
                rows={3}
                maxLength={500}
                value={buyerGoal}
                onChange={(event) => setBuyerGoal(event.target.value)}
                disabled={Boolean(mandate) || busy}
              />
            </label>

            <fieldset disabled={Boolean(mandate) || busy}>
              <legend>Allowed categories</legend>
              <div className="chip-grid">
                {CATEGORIES.map((category) => (
                  <label className="choice-chip" key={category}>
                    <input
                      type="checkbox"
                      checked={allowedCategories.includes(category)}
                      onChange={() =>
                        setAllowedCategories((current) =>
                          toggleValue(current, category)
                        )
                      }
                    />
                    {category}
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset disabled={Boolean(mandate) || busy}>
              <legend>Required compatibility</legend>
              <div className="chip-grid">
                {COMPATIBILITY.map((tag) => (
                  <label className="choice-chip" key={tag}>
                    <input
                      type="checkbox"
                      checked={requiredCompatibility.includes(tag)}
                      onChange={() =>
                        setRequiredCompatibility((current) =>
                          toggleValue(current, tag)
                        )
                      }
                    />
                    {tag}
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset disabled={Boolean(mandate) || busy}>
              <legend>Cross-sell permission</legend>
              <label className="choice-chip">
                <input
                  type="checkbox"
                  checked={crossSellEnabled}
                  onChange={(event) =>
                    setMaxCrossSellPercentage(event.target.checked ? 20 : 0)
                  }
                />
                Allow AI to add one eligible companion
              </label>
            </fieldset>

            <div className="split-fields">
              <label>
                Max cross-sell % of budget
                <input
                  type="number"
                  min="1"
                  max="30"
                  value={maxCrossSellPercentage}
                  onChange={(event) =>
                    setMaxCrossSellPercentage(event.target.value)
                  }
                  disabled={Boolean(mandate) || busy || !crossSellEnabled}
                />
              </label>

              <label>
                Expires in minutes
                <input
                  type="number"
                  min="1"
                  max="1440"
                  value={expiresInMinutes}
                  onChange={(event) =>
                    setExpiresInMinutes(event.target.value)
                  }
                  disabled={Boolean(mandate) || busy}
                />
              </label>
            </div>

            {!mandate ? (
              <button
                className="primary-action"
                type="submit"
                disabled={!canCreateMandate || busy}
              >
                <LockKeyhole size={17} />
                {status === "creating_mandate"
                  ? "Approving mandate…"
                  : "Approve immutable mandate"}
              </button>
            ) : (
              <div className="approved-box">
                <CheckCircle2 size={18} />
                <div>
                  <strong>Mandate approved</strong>
                  <code>{mandate.mandate_id}</code>
                </div>
              </div>
            )}
          </form>
        </div>

        <div className="delegation-column">
          <section className="delegation-card">
            <div className="section-heading">
              <div>
                <span className="eyebrow">2. Delegated decision</span>
                <h2>Let CartPilot choose</h2>
                <p>The AI only sees deterministic eligible SKU options.</p>
              </div>
              <Bot size={22} />
            </div>

            <label className="task-field">
              Shopping task
              <textarea
                rows={3}
                maxLength={500}
                value={task}
                onChange={(event) => setTask(event.target.value)}
                disabled={!mandate || busy || Boolean(purchase)}
              />
            </label>

            {!purchase && (
              <button
                className="primary-action"
                type="button"
                onClick={handleDelegatePurchase}
                disabled={!mandate || busy}
              >
                <Sparkles size={17} />
                {status === "planning" ? "AI buyer is choosing…" : "Let AI Buyer Choose"}
              </button>
            )}

            {purchase && (
              <div className="purchase-result">
                <div className="result-banner">
                  <PackageCheck size={20} />
                  <div>
                    <span>AI selected</span>
                    <strong>{purchase.base_product.name}</strong>
                  </div>
                  <b>{formatMoney(purchase.quote.base_price_paise)}</b>
                </div>

                <p className="plan-reason">{purchase.plan.reason}</p>

                <div className="result-meta">
                  <span>SKU {purchase.plan.base_product_sku}</span>
                  <span>
                    Confidence {Math.round(purchase.plan.confidence * 100)}%
                  </span>
                </div>

                {purchase.cross_sell_product && (
                  <div className="add-on-box">
                    <span>AI also chose an eligible add-on</span>
                    <strong>{purchase.cross_sell_product.name}</strong>
                    <b>
                      +{formatMoney(purchase.quote.upsell_price_paise)}
                    </b>
                  </div>
                )}

                <div className="quote-summary">
                  <div>
                    <span>Total immutable quote</span>
                    <small>
                      <Clock3 size={13} /> valid until{" "}
                      {new Date(purchase.quote.expires_at).toLocaleTimeString()}
                    </small>
                  </div>
                  <strong>{formatMoney(purchase.quote.total_paise)}</strong>
                </div>

                <div className="safety-trace">
                  <strong>Why this is safe</strong>
                  <ul>
                    {purchase.decision_trace.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ul>
                </div>

                <button
                  className="pay-action"
                  type="button"
                  onClick={handleConfirmAndPay}
                  disabled={busy || paymentStatus !== "idle"}
                >
                  {paymentStatus === "verified" || paymentStatus === "recovered" ? (
                    <CheckCircle2 size={18} />
                  ) : (
                    <LockKeyhole size={18} />
                  )}
                  {paymentStatus === "creating_order"
                    ? "Creating order…"
                    : paymentStatus === "checkout_open"
                      ? "Opening Razorpay…"
                      : paymentStatus === "verifying"
                        ? "Verifying payment…"
                        : paymentStatus === "pending"
                          ? "Waiting for server confirmation"
                          : paymentStatus === "verified"
                            ? "Payment verified"
                            : paymentStatus === "recovered"
                              ? "Payment recovered by webhook"
                              : "Confirm & Pay"}
                </button>
              </div>
            )}
          </section>

          <section className="execution-card">
            <span className="eyebrow">Mandate execution ledger</span>
            {!execution ? (
              <p>No delegated execution yet.</p>
            ) : (
              <div className="execution-details">
                <div><span>Status</span><strong>{execution.status}</strong></div>
                <div><span>Reserved</span><strong>{formatMoney(execution.reserved_paise)}</strong></div>
                <div><span>Committed</span><strong>{execution.committed_paise ? formatMoney(execution.committed_paise) : "—"}</strong></div>
                <div><span>Execution</span><code>{execution.execution_id}</code></div>
              </div>
            )}
          </section>

          <section className="audit-card">
            <span className="eyebrow">Mandate audit trail</span>
            {!audit ? (
              <p>Create a mandate to see policy decisions.</p>
            ) : audit.events.length === 0 ? (
              <p>No events recorded yet.</p>
            ) : (
              <ol>
                {audit.events.slice(-8).map((event) => (
                  <li key={event.event_id}>
                    <span>{event.actor}</span>
                    <p>{event.explanation}</p>
                    <code>{event.reason_code}</code>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      </section>

      {error && <div className="floating-error" role="alert">{error}</div>}

      {(mandate || purchase) && (
        <button className="reset-button" type="button" onClick={resetFlow} disabled={busy}>
          Start a new delegated purchase
        </button>
      )}
    </main>
  );
}

export default AiBuyer;
