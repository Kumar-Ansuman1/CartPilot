import { useEffect, useRef, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Clock3,
  LockKeyhole,
  PackageCheck,
  Send,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  User,
} from "lucide-react";

import {
  confirmCheckout,
  requestShoppingQuote,
  verifyPayment,
} from "./api";
import { openRazorpayCheckout } from "./razorpay";
import "./App.css";


const EXAMPLE_REQUESTS = [
  "USB-C charger for Android under 2000 rupees",
  "iPhone 15 cable under 1500 rupees",
  "Bluetooth earbuds under 3000 rupees",
];


function formatMoney(amountPaise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
  }).format(amountPaise / 100);
}


function QuoteCard({
  result,
  isActive,
  paymentStatus,
  onCheckout,
}) {
  const { base_product, upsell_product, quote } = result;

  const buttonLabels = {
    idle: "Confirm & Pay",
    creating_order: "Creating secure order…",
    opening_checkout: "Opening Razorpay…",
    verifying: "Verifying payment…",
    success: "Payment verified",
  };

  const isProcessing = ![
    "idle",
    "success",
  ].includes(paymentStatus);

  return (
    <article className="quote-card">
      <div className="quote-heading">
        <div>
          <span className="eyebrow">
            Recommended match
          </span>

          <h3>{base_product.name}</h3>

          <p>{base_product.description}</p>
        </div>

        <span className="price">
          {formatMoney(quote.base_price_paise)}
        </span>
      </div>

      <div className="product-meta">
        <span>{base_product.category}</span>
        <span>{base_product.sku}</span>
        <span>In stock</span>
      </div>

      {upsell_product && (
        <div className="upsell">
          <div>
            <span className="eyebrow">
              Optional add-on
            </span>

            <strong>{upsell_product.name}</strong>

            <p>{upsell_product.description}</p>
          </div>

          <span>
            +{formatMoney(quote.upsell_price_paise)}
          </span>
        </div>
      )}

      <div className="quote-total">
        <div>
          <span>Total</span>

          <small>
            <Clock3 size={14} />
            Quote valid until{" "}
            {new Date(
              quote.expires_at
            ).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </small>
        </div>

        <strong>
          {formatMoney(quote.total_paise)}
        </strong>
      </div>

      <details className="decision-details">
        <summary>
          <ShieldCheck size={16} />
          Why this action is safe
        </summary>

        <ul>
          {result.decision_trace.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      </details>

      <button
        className="checkout-button"
        type="button"
        disabled={
          !isActive ||
          isProcessing ||
          paymentStatus === "success"
        }
        onClick={onCheckout}
      >
        {paymentStatus === "success" ? (
          <CheckCircle2 size={18} />
        ) : (
          <LockKeyhole size={18} />
        )}

        {buttonLabels[paymentStatus]}
      </button>

      <p className="confirmation-note">
        Nothing is charged until you confirm and complete
        Razorpay Checkout.
      </p>
    </article>
  );
}


function App() {
  const [input, setInput] = useState("");

  const [messages, setMessages] = useState([
    {
      id: "welcome",
      role: "assistant",
      text:
        "Tell me what accessory you need, your device, " +
        "and your maximum budget.",
    },
  ]);

  const [clarificationContext, setClarificationContext] =
    useState("");

  const [activeResult, setActiveResult] =
    useState(null);

  const [isThinking, setIsThinking] = useState(false);

  const [paymentStatus, setPaymentStatus] =
    useState("idle");

  const [error, setError] = useState("");

  const messagesContainerRef = useRef(null);
  const requestInFlightRef = useRef(false);
  const checkoutInFlightRef = useRef(false);

  useEffect(() => {
    const container = messagesContainerRef.current;

    if (container) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [messages, isThinking]);

  async function handleSubmit(event) {
    event.preventDefault();

    const submittedMessage = input.trim();

    if (
      submittedMessage.length < 3 ||
      isThinking ||
      requestInFlightRef.current
    ) {
      return;
    }

    requestInFlightRef.current = true;

    const completeMessage = clarificationContext
      ? `${clarificationContext}. Additional details: ${submittedMessage}`
      : submittedMessage;

    setMessages((current) => [
      ...current,
      {
        id: `${Date.now()}-user`,
        role: "user",
        text: submittedMessage,
      },
    ]);

    setInput("");
    setError("");
    setIsThinking(true);
    setPaymentStatus("idle");

    try {
      const result = await requestShoppingQuote(
        completeMessage
      );

      if (
        result.status === "clarification_required"
      ) {
        setClarificationContext(completeMessage);
        setActiveResult(null);

        setMessages((current) => [
          ...current,
          {
            id: `${Date.now()}-clarification`,
            role: "assistant",
            text: result.message,
          },
        ]);

        return;
      }

      setClarificationContext("");

      if (result.status === "no_match") {
        setActiveResult(null);

        setMessages((current) => [
          ...current,
          {
            id: `${Date.now()}-no-match`,
            role: "assistant",
            text: result.message,
          },
        ]);

        return;
      }

      setActiveResult(result);

      setMessages((current) => [
        ...current,
        {
          id: `${Date.now()}-quote`,
          role: "assistant",
          text: result.message,
          result,
        },
      ]);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      requestInFlightRef.current = false;
      setIsThinking(false);
    }
  }

  async function handleCheckout(result) {
    if (
      paymentStatus !== "idle" ||
      checkoutInFlightRef.current
    ) {
      return;
    }

    checkoutInFlightRef.current = true;
    setError("");

    try {
      setPaymentStatus("creating_order");

      const checkoutOrder = await confirmCheckout(
        result.quote.quote_id
      );

      setPaymentStatus("opening_checkout");

      const razorpayResponse =
        await openRazorpayCheckout({
          checkoutOrder,
          productName: result.base_product.name,
        });

      setPaymentStatus("verifying");

      const verifiedPayment = await verifyPayment(
        result.quote.quote_id,
        razorpayResponse
      );

      setPaymentStatus("success");

      setMessages((current) => [
        ...current,
        {
          id: `${Date.now()}-success`,
          role: "assistant",
          text:
            "Payment verified successfully. Payment ID: " +
            verifiedPayment.razorpay_payment_id,
          success: true,
        },
      ]);
    } catch (checkoutError) {
      setPaymentStatus("idle");
      setError(checkoutError.message);
    } finally {
      checkoutInFlightRef.current = false;
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-icon">
            <ShoppingBag size={20} />
          </span>

          <span>
            <strong>VoltCart</strong>
            <small>Powered by CartPilot</small>
          </span>
        </a>

        <span className="test-badge">
          <span />
          Razorpay Test Mode
        </span>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <span className="hero-label">
            <Sparkles size={15} />
            Safe agentic shopping
          </span>

          <h1>
            Find the right accessory.
            <span> Stay in control.</span>
          </h1>

          <p>
            Describe what you need. CartPilot searches a
            trusted catalog, respects your budget and asks
            before creating a payment order.
          </p>
        </div>

        <div className="workspace">
          <section className="chat-panel">
            <div className="panel-heading">
              <div>
                <h2>Shopping assistant</h2>

                <p>
                  AI understands. Code controls the money.
                </p>
              </div>

              <Bot size={22} />
            </div>

            <div
              className="messages"
              ref={messagesContainerRef}
            >
              {messages.map((message) => (
                <div
                  className={`message ${message.role}`}
                  key={message.id}
                >
                  <span className="avatar">
                    {message.role === "assistant" ? (
                      <Bot size={17} />
                    ) : (
                      <User size={17} />
                    )}
                  </span>

                  <div className="message-content">
                    {message.text && (
                      <p>{message.text}</p>
                    )}

                    {message.result && (
                      <QuoteCard
                        result={message.result}
                        isActive={
                          activeResult?.quote.quote_id ===
                          message.result.quote.quote_id
                        }
                        paymentStatus={
                          activeResult?.quote.quote_id ===
                          message.result.quote.quote_id
                            ? paymentStatus
                            : "idle"
                        }
                        onCheckout={() =>
                          handleCheckout(message.result)
                        }
                      />
                    )}

                    {message.success && (
                      <span className="success-label">
                        <CheckCircle2 size={15} />
                        Server verified
                      </span>
                    )}
                  </div>
                </div>
              ))}

              {isThinking && (
                <div className="message assistant">
                  <span className="avatar">
                    <Bot size={17} />
                  </span>

                  <div className="typing">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              )}
            </div>

            {error && (
              <div
                className="error-message"
                role="alert"
              >
                {error}
              </div>
            )}

            <form
              className="prompt-form"
              onSubmit={handleSubmit}
            >
              <textarea
                value={input}
                onChange={(event) =>
                  setInput(event.target.value)
                }
                placeholder={
                  clarificationContext
                    ? "Add the missing details…"
                    : "Example: USB-C charger under ₹2,000"
                }
                maxLength={500}
                rows={2}
                disabled={isThinking}
                aria-label="Shopping request"
              />

              <button
                type="submit"
                disabled={
                  input.trim().length < 3 ||
                  isThinking
                }
                aria-label="Send shopping request"
              >
                <Send size={18} />
              </button>
            </form>

            <div className="examples">
              {EXAMPLE_REQUESTS.map((example) => (
                <button
                  type="button"
                  key={example}
                  onClick={() => setInput(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          </section>

          <aside className="safety-panel">
            <span className="eyebrow">
              Control layer
            </span>

            <h2>Every money action is gated.</h2>

            <div className="safety-item">
              <ShieldCheck />

              <div>
                <strong>
                  Bounded recommendations
                </strong>

                <p>
                  Budget, compatibility and stock are
                  checked by deterministic code.
                </p>
              </div>
            </div>

            <div className="safety-item">
              <PackageCheck />

              <div>
                <strong>Trusted pricing</strong>

                <p>
                  Checkout amounts come from server-stored
                  quotes, never the browser.
                </p>
              </div>
            </div>

            <div className="safety-item">
              <LockKeyhole />

              <div>
                <strong>Verified payments</strong>

                <p>
                  Razorpay signatures are verified before a
                  payment is recorded.
                </p>
              </div>
            </div>

            <div className="boundary-card">
              <span>AI may</span>
              <strong>Understand intent</strong>

              <span>AI may not</span>
              <strong>Control money</strong>
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

export default App;