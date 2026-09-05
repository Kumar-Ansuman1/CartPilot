import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Bot,
  CheckCircle2,
  Clock3,
  History,
  LockKeyhole,
  PackageCheck,
  RefreshCw,
  Send,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  User,
} from "lucide-react";

import {
  acceptCrossSell,
  confirmCheckout,
  declineCrossSell,
  getShoppingAudit,
  selectBaseProduct,
  startShoppingSession,
  verifyPayment,
} from "./api";
import {
  openRazorpayCheckout,
} from "./razorpay";
import "./App.css";


const EXAMPLE_REQUESTS = [
  "USB-C charger for Android under 2000 rupees",
  "iPhone 15 case under 1500 rupees",
  "Bluetooth earbuds under 3000 rupees",
];


function formatMoney(amountPaise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
  }).format(amountPaise / 100);
}


function formatTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString(
    [],
    {
      hour: "2-digit",
      minute: "2-digit",
    }
  );
}


function createMessageId(label) {
  return (
    `${Date.now()}-${label}-` +
    Math.random().toString(16).slice(2)
  );
}


function DecisionDetails({ steps }) {
  if (!steps?.length) {
    return null;
  }

  return (
    <details className="decision-details">
      <summary>
        <ShieldCheck size={16} />
        Why this action is safe
      </summary>

      <ul>
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ul>
    </details>
  );
}


const AUDIT_ACTOR_LABELS = {
  buyer: "You",
  ai: "AI",
  deterministic_core: "Commerce core",
  razorpay: "Razorpay",
};


function AuditTimeline({
  timeline,
  status,
  error,
  onRefresh,
}) {
  const events = timeline?.events ?? [];

  return (
    <section
      className="audit-panel"
      aria-labelledby="audit-heading"
    >
      <div className="audit-heading">
        <div>
          <span className="eyebrow">
            Commerce flight recorder
          </span>

          <h2 id="audit-heading">
            Why did CartPilot do this?
          </h2>
        </div>

        {timeline && (
          <button
            className="audit-refresh"
            type="button"
            onClick={onRefresh}
            disabled={status === "loading"}
            aria-label="Refresh audit timeline"
            title="Refresh audit timeline"
          >
            <RefreshCw
              size={16}
              className={
                status === "loading"
                  ? "is-spinning"
                  : ""
              }
            />
          </button>
        )}
      </div>

      {!timeline && status === "idle" && (
        <div className="audit-empty">
          <History size={20} />

          <p>
            Start a shopping request to see each
            AI, buyer, policy and payment action.
          </p>
        </div>
      )}

      {!timeline && status === "loading" && (
        <p
          className="audit-status"
          role="status"
        >
          Loading the decision history…
        </p>
      )}

      {timeline && (
        <ol className="audit-events">
          {events.map((event) => (
            <li
              className={
                `audit-event ${event.outcome}`
              }
              key={event.event_id}
            >
              <span
                className="audit-marker"
                aria-hidden="true"
              >
                {event.outcome === "rejected" ||
                event.outcome === "failed"
                  ? "!"
                  : "✓"}
              </span>

              <div>
                <div className="audit-event-meta">
                  <span>
                    {AUDIT_ACTOR_LABELS[
                      event.actor
                    ] ?? event.actor}
                  </span>

                  <time
                    dateTime={event.created_at}
                  >
                    {formatTime(event.created_at)}
                  </time>
                </div>

                <p>{event.explanation}</p>

                <div className="audit-event-details">
                  <code>{event.reason_code}</code>

                  {event.amount_paise !== null &&
                    event.amount_paise !==
                      undefined && (
                      <strong>
                        {formatMoney(
                          event.amount_paise
                        )}
                      </strong>
                    )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      {timeline && events.length === 0 && (
        <p className="audit-status">
          No events have been recorded yet.
        </p>
      )}

      {error && (
        <div
          className="audit-error"
          role="status"
        >
          <p>{error}</p>

          <button
            type="button"
            onClick={onRefresh}
            disabled={status === "loading"}
          >
            Try again
          </button>
        </div>
      )}

      {timeline?.quote_id && (
        <p className="audit-quote-link">
          Linked to quote{" "}
          <code>{timeline.quote_id}</code>
        </p>
      )}
    </section>
  );
}


function BaseSelectionCard({
  result,
  isActive,
  selectionStatus,
  pendingSku,
  onSelect,
}) {
  return (
    <article className="selection-card">
      <div className="selection-heading">
        <div>
          <span className="eyebrow">
            Choose your product
          </span>

          <h3>Top catalog matches</h3>

          <p>
            No product has been selected for you.
            Review the options and choose one.
          </p>
        </div>

        <span className="option-count">
          {result.base_product_options.length}
          {" "}
          options
        </span>
      </div>

      <div className="product-options">
        {result.base_product_options.map(
          (product) => {
            const isRecommended =
              product.sku ===
              result.recommended_base_product_sku;

            const isPending =
              selectionStatus ===
                "selecting_base" &&
              pendingSku === product.sku;

            return (
              <div
                className="product-option"
                key={product.sku}
              >
                <div className="product-option-heading">
                  <div>
                    {isRecommended && (
                      <span className="recommended-badge">
                        Best match
                      </span>
                    )}

                    <h4>{product.name}</h4>
                  </div>

                  <strong>
                    {formatMoney(
                      product.price_paise
                    )}
                  </strong>
                </div>

                <p>{product.description}</p>

                <div className="product-meta">
                  <span>{product.category}</span>
                  <span>{product.sku}</span>
                  <span>In stock</span>
                </div>

                <button
                  className="selection-button"
                  type="button"
                  disabled={
                    !isActive ||
                    selectionStatus !== "idle"
                  }
                  onClick={() =>
                    onSelect(product)
                  }
                >
                  <PackageCheck size={17} />

                  {isPending
                    ? "Selecting…"
                    : "Choose this product"}
                </button>
              </div>
            );
          }
        )}
      </div>

      <div className="session-expiry">
        <Clock3 size={14} />

        Choose before{" "}
        {formatTime(
          result.session_expires_at
        )}
      </div>

      <DecisionDetails
        steps={result.decision_trace}
      />
    </article>
  );
}


function CrossSellDecisionCard({
  result,
  isActive,
  selectionStatus,
  pendingSku,
  onAccept,
  onDecline,
}) {
  const hasOptions =
    result.cross_sell_options.length > 0;

  return (
    <article className="selection-card">
      <div className="selected-base-summary">
        <div>
          <span className="eyebrow">
            Selected product
          </span>

          <h3>
            {result.selected_base_product.name}
          </h3>

          <p>
            {
              result.selected_base_product
                .description
            }
          </p>
        </div>

        <strong>
          {formatMoney(
            result.selected_base_product
              .price_paise
          )}
        </strong>
      </div>

      <div className="cross-sell-heading">
        <div>
          <span className="eyebrow">
            Optional add-on
          </span>

          <h4>
            {hasOptions
              ? "Would you like to add one?"
              : "No eligible add-on found"}
          </h4>

          <p>
            {hasOptions
              ? (
                "Nothing is preselected. " +
                "You may add one offered item " +
                "or continue without it."
              )
              : (
                "You can continue with only " +
                "your selected product."
              )}
          </p>
        </div>
      </div>

      {hasOptions && (
        <div className="product-options">
          {result.cross_sell_options.map(
            (product) => {
              const isPending =
                selectionStatus ===
                  "finalizing_cross_sell" &&
                pendingSku === product.sku;

              return (
                <div
                  className="product-option compact"
                  key={product.sku}
                >
                  <div className="product-option-heading">
                    <div>
                      <h4>{product.name}</h4>

                      <span className="cross-category-label">
                        Optional companion
                      </span>
                    </div>

                    <strong>
                      +
                      {formatMoney(
                        product.price_paise
                      )}
                    </strong>
                  </div>

                  <p>{product.description}</p>

                  <div className="product-meta">
                    <span>
                      {product.category}
                    </span>
                    <span>{product.sku}</span>
                    <span>In stock</span>
                  </div>

                  <button
                    className="selection-button"
                    type="button"
                    disabled={
                      !isActive ||
                      selectionStatus !== "idle"
                    }
                    onClick={() =>
                      onAccept(product)
                    }
                  >
                    <PackageCheck size={17} />

                    {isPending
                      ? "Creating quote…"
                      : "Add this item"}
                  </button>
                </div>
              );
            }
          )}
        </div>
      )}

      <button
        className="decline-button"
        type="button"
        disabled={
          !isActive ||
          selectionStatus !== "idle"
        }
        onClick={onDecline}
      >
        {selectionStatus ===
          "finalizing_cross_sell" &&
        pendingSku === null
          ? "Creating quote…"
          : "Continue without an add-on"}
      </button>

      <p className="confirmation-note">
        A quote is created only after you accept
        an offered add-on or explicitly decline.
      </p>

      <div className="session-expiry">
        <Clock3 size={14} />

        Decide before{" "}
        {formatTime(
          result.session_expires_at
        )}
      </div>

      <DecisionDetails
        steps={result.decision_trace}
      />
    </article>
  );
}


function QuoteCard({
  quoteView,
  isActive,
  paymentStatus,
  onCheckout,
}) {
  const {
    result,
    baseProduct,
    crossSellProduct,
  } = quoteView;

  const { quote } = result;

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
            Your selected product
          </span>

          <h3>{baseProduct.name}</h3>

          <p>{baseProduct.description}</p>
        </div>

        <span className="price">
          {formatMoney(
            quote.base_price_paise
          )}
        </span>
      </div>

      <div className="product-meta">
        <span>{baseProduct.category}</span>
        <span>{baseProduct.sku}</span>
        <span>In stock</span>
      </div>

      {crossSellProduct && (
        <div className="upsell">
          <div>
            <span className="eyebrow">
              Accepted add-on
            </span>

            <strong>
              {crossSellProduct.name}
            </strong>

            <p>
              {crossSellProduct.description}
            </p>
          </div>

          <span>
            +
            {formatMoney(
              quote.upsell_price_paise
            )}
          </span>
        </div>
      )}

      {!crossSellProduct && (
        <div className="declined-summary">
          <CheckCircle2 size={17} />

          <span>
            You chose to continue without an
            add-on.
          </span>
        </div>
      )}

      <div className="quote-total">
        <div>
          <span>Total</span>

          <small>
            <Clock3 size={14} />

            Quote valid until{" "}
            {formatTime(quote.expires_at)}
          </small>
        </div>

        <strong>
          {formatMoney(
            quote.total_paise
          )}
        </strong>
      </div>

      <DecisionDetails
        steps={result.decision_trace}
      />

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
        Nothing is charged until you explicitly
        confirm and complete Razorpay Checkout.
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
        "Tell me what accessory you need, " +
        "your device, and your maximum budget.",
    },
  ]);

  const [
    clarificationContext,
    setClarificationContext,
  ] = useState("");

  const [
    activeSession,
    setActiveSession,
  ] = useState(null);

  const [
    activeQuote,
    setActiveQuote,
  ] = useState(null);

  const [isThinking, setIsThinking] =
    useState(false);

  const [
    selectionStatus,
    setSelectionStatus,
  ] = useState("idle");

  const [pendingSku, setPendingSku] =
    useState(null);

  const [
    paymentStatus,
    setPaymentStatus,
  ] = useState("idle");

  const [error, setError] = useState("");

  const [auditTimeline, setAuditTimeline] =
    useState(null);
  const [auditStatus, setAuditStatus] =
    useState("idle");
  const [auditError, setAuditError] =
    useState("");

  const messagesContainerRef = useRef(null);
  const requestInFlightRef = useRef(false);
  const actionInFlightRef = useRef(false);
  const checkoutInFlightRef = useRef(false);
  const auditRequestIdRef = useRef(0);

  async function refreshAudit(sessionId) {
    if (!sessionId) {
      return;
    }

    const requestId =
      auditRequestIdRef.current + 1;
    auditRequestIdRef.current = requestId;
    setAuditStatus("loading");
    setAuditError("");

    try {
      const timeline =
        await getShoppingAudit(sessionId);

      if (
        auditRequestIdRef.current === requestId
      ) {
        setAuditTimeline(timeline);
        setAuditStatus("success");
      }
    } catch (auditRequestError) {
      if (
        auditRequestIdRef.current === requestId
      ) {
        setAuditStatus("error");
        setAuditError(
          auditRequestError.message
        );
      }
    }
  }

  useEffect(() => {
    const container =
      messagesContainerRef.current;

    if (container) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [
    messages,
    isThinking,
    selectionStatus,
    paymentStatus,
  ]);

  async function handleSubmit(event) {
    event.preventDefault();

    const submittedMessage = input.trim();

    if (
      submittedMessage.length < 3 ||
      isThinking ||
      requestInFlightRef.current ||
      actionInFlightRef.current ||
      checkoutInFlightRef.current
    ) {
      return;
    }

    requestInFlightRef.current = true;

    const completeMessage =
      clarificationContext
        ? (
          `${clarificationContext}. ` +
          `Additional details: ${submittedMessage}`
        )
        : submittedMessage;

    setMessages((current) => [
      ...current,
      {
        id: createMessageId("user"),
        role: "user",
        text: submittedMessage,
      },
    ]);

    setInput("");
    setError("");
    setIsThinking(true);
    setPaymentStatus("idle");
    setSelectionStatus("idle");
    setPendingSku(null);
    setActiveSession(null);
    setActiveQuote(null);
    auditRequestIdRef.current += 1;
    setAuditTimeline(null);
    setAuditStatus("idle");
    setAuditError("");

    try {
      const result =
        await startShoppingSession(
          completeMessage
        );

      if (
        result.status ===
        "clarification_required"
      ) {
        setClarificationContext(
          completeMessage
        );

        setMessages((current) => [
          ...current,
          {
            id: createMessageId(
              "clarification"
            ),
            role: "assistant",
            text: result.message,
          },
        ]);

        return;
      }

      setClarificationContext("");

      if (result.status === "no_match") {
        setMessages((current) => [
          ...current,
          {
            id: createMessageId(
              "no-match"
            ),
            role: "assistant",
            text: result.message,
          },
        ]);

        return;
      }

      if (
        result.status !==
        "base_selection_required"
      ) {
        throw new Error(
          "The backend returned an unexpected " +
            "shopping state."
        );
      }

      setActiveSession({
        sessionId: result.session_id,
        stage: "base_selection",
      });
      void refreshAudit(result.session_id);

      setMessages((current) => [
        ...current,
        {
          id: createMessageId(
            "base-options"
          ),
          role: "assistant",
          text: result.message,
          baseSelection: result,
        },
      ]);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      requestInFlightRef.current = false;
      setIsThinking(false);
    }
  }

  async function handleBaseSelection(
    result,
    product
  ) {
    if (
      actionInFlightRef.current ||
      selectionStatus !== "idle" ||
      activeSession?.stage !==
        "base_selection" ||
      activeSession?.sessionId !==
        result.session_id
    ) {
      return;
    }

    actionInFlightRef.current = true;
    setError("");
    setSelectionStatus("selecting_base");
    setPendingSku(product.sku);

    try {
      const selectionResult =
        await selectBaseProduct(
          result.session_id,
          product.sku
        );

      setActiveSession({
        sessionId:
          selectionResult.session_id,
        stage: "cross_sell_decision",
      });
      void refreshAudit(
        selectionResult.session_id
      );

      setMessages((current) => [
        ...current,
        {
          id: createMessageId(
            "cross-sell-options"
          ),
          role: "assistant",
          text: selectionResult.message,
          crossSellSelection:
            selectionResult,
        },
      ]);
    } catch (selectionError) {
      setError(selectionError.message);
      void refreshAudit(result.session_id);
    } finally {
      actionInFlightRef.current = false;
      setSelectionStatus("idle");
      setPendingSku(null);
    }
  }

  async function handleCrossSellDecision(
    result,
    decision,
    product = null
  ) {
    if (
      actionInFlightRef.current ||
      selectionStatus !== "idle" ||
      activeSession?.stage !==
        "cross_sell_decision" ||
      activeSession?.sessionId !==
        result.session_id
    ) {
      return;
    }

    actionInFlightRef.current = true;
    setError("");
    setSelectionStatus(
      "finalizing_cross_sell"
    );
    setPendingSku(product?.sku ?? null);

    try {
      const quoteResult =
        decision === "accept"
          ? await acceptCrossSell(
              result.session_id,
              product.sku
            )
          : await declineCrossSell(
              result.session_id
            );

      const quoteView = {
        result: quoteResult,
        baseProduct:
          result.selected_base_product,
        crossSellProduct:
          decision === "accept"
            ? product
            : null,
      };

      setActiveSession({
        sessionId: quoteResult.session_id,
        stage: "quote_review",
      });
      void refreshAudit(
        quoteResult.session_id
      );
      setActiveQuote(quoteView);
      setPaymentStatus("idle");

      setMessages((current) => [
        ...current,
        {
          id: createMessageId("quote"),
          role: "assistant",
          text: quoteResult.message,
          quoteView,
        },
      ]);
    } catch (selectionError) {
      setError(selectionError.message);
      void refreshAudit(result.session_id);
    } finally {
      actionInFlightRef.current = false;
      setSelectionStatus("idle");
      setPendingSku(null);
    }
  }

  async function handleCheckout(
    quoteView
  ) {
    if (
      paymentStatus !== "idle" ||
      checkoutInFlightRef.current ||
      activeQuote?.result.quote.quote_id !==
        quoteView.result.quote.quote_id
    ) {
      return;
    }

    checkoutInFlightRef.current = true;
    setError("");

    try {
      setPaymentStatus(
        "creating_order"
      );

      const checkoutOrder =
        await confirmCheckout(
          quoteView.result.quote.quote_id
        );

      void refreshAudit(
        quoteView.result.session_id
      );

      setPaymentStatus(
        "opening_checkout"
      );

      const razorpayResponse =
        await openRazorpayCheckout({
          checkoutOrder,
          productName:
            quoteView.baseProduct.name,
        });

      setPaymentStatus("verifying");

      const verifiedPayment =
        await verifyPayment(
          quoteView.result.quote.quote_id,
          razorpayResponse
        );

      setPaymentStatus("success");
      void refreshAudit(
        quoteView.result.session_id
      );

      setActiveSession((current) => (
        current
          ? {
              ...current,
              stage: "payment_verified",
            }
          : current
      ));

      setMessages((current) => [
        ...current,
        {
          id: createMessageId("success"),
          role: "assistant",
          text:
            "Payment verified successfully. " +
            "Payment ID: " +
            verifiedPayment
              .razorpay_payment_id,
          success: true,
        },
      ]);
    } catch (checkoutError) {
      setPaymentStatus("idle");
      setError(checkoutError.message);
      void refreshAudit(
        quoteView.result.session_id
      );
    } finally {
      checkoutInFlightRef.current = false;
    }
  }

  const paymentIsProcessing = ![
    "idle",
    "success",
  ].includes(paymentStatus);

  const inputIsDisabled =
    isThinking ||
    selectionStatus !== "idle" ||
    paymentIsProcessing;

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-icon">
            <ShoppingBag size={20} />
          </span>

          <span>
            <strong>VoltCart</strong>
            <small>
              Powered by CartPilot
            </small>
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
            Describe what you need, choose your
            product, decide whether to add an
            optional companion, and review the
            final quote before checkout.
          </p>
        </div>

        <div className="workspace">
          <section className="chat-panel">
            <div className="panel-heading">
              <div>
                <h2>Shopping assistant</h2>

                <p>
                  AI understands. You choose.
                  Code controls the money.
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
                  className={
                    `message ${message.role}`
                  }
                  key={message.id}
                >
                  <span className="avatar">
                    {message.role ===
                    "assistant" ? (
                      <Bot size={17} />
                    ) : (
                      <User size={17} />
                    )}
                  </span>

                  <div className="message-content">
                    {message.text && (
                      <p>{message.text}</p>
                    )}

                    {message.baseSelection && (
                      <BaseSelectionCard
                        result={
                          message.baseSelection
                        }
                        isActive={
                          activeSession?.stage ===
                            "base_selection" &&
                          activeSession?.sessionId ===
                            message.baseSelection
                              .session_id
                        }
                        selectionStatus={
                          selectionStatus
                        }
                        pendingSku={pendingSku}
                        onSelect={(product) =>
                          handleBaseSelection(
                            message.baseSelection,
                            product
                          )
                        }
                      />
                    )}

                    {message.crossSellSelection && (
                      <CrossSellDecisionCard
                        result={
                          message
                            .crossSellSelection
                        }
                        isActive={
                          activeSession?.stage ===
                            "cross_sell_decision" &&
                          activeSession?.sessionId ===
                            message
                              .crossSellSelection
                              .session_id
                        }
                        selectionStatus={
                          selectionStatus
                        }
                        pendingSku={pendingSku}
                        onAccept={(product) =>
                          handleCrossSellDecision(
                            message
                              .crossSellSelection,
                            "accept",
                            product
                          )
                        }
                        onDecline={() =>
                          handleCrossSellDecision(
                            message
                              .crossSellSelection,
                            "decline"
                          )
                        }
                      />
                    )}

                    {message.quoteView && (
                      <QuoteCard
                        quoteView={
                          message.quoteView
                        }
                        isActive={
                          activeQuote?.result
                            .quote.quote_id ===
                          message.quoteView
                            .result.quote.quote_id
                        }
                        paymentStatus={
                          activeQuote?.result
                            .quote.quote_id ===
                          message.quoteView
                            .result.quote.quote_id
                            ? paymentStatus
                            : "idle"
                        }
                        onCheckout={() =>
                          handleCheckout(
                            message.quoteView
                          )
                        }
                      />
                    )}

                    {message.success && (
                      <span className="success-label">
                        <CheckCircle2
                          size={15}
                        />
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
                  setInput(
                    event.target.value
                  )
                }
                placeholder={
                  clarificationContext
                    ? (
                      "Add the missing " +
                      "details…"
                    )
                    : (
                      "Example: USB-C charger " +
                      "under ₹2,000"
                    )
                }
                maxLength={500}
                rows={2}
                disabled={inputIsDisabled}
                aria-label="Shopping request"
              />

              <button
                type="submit"
                disabled={
                  input.trim().length < 3 ||
                  inputIsDisabled
                }
                aria-label="Send shopping request"
              >
                <Send size={18} />
              </button>
            </form>

            <div className="examples">
              {EXAMPLE_REQUESTS.map(
                (example) => (
                  <button
                    type="button"
                    key={example}
                    disabled={inputIsDisabled}
                    onClick={() =>
                      setInput(example)
                    }
                  >
                    {example}
                  </button>
                )
              )}
            </div>
          </section>

          <aside className="insight-column">
            <section className="safety-panel">
            <span className="eyebrow">
              Control layer
            </span>

            <h2>
              Every money action is gated.
            </h2>

            <div className="safety-item">
              <ShieldCheck />

              <div>
                <strong>
                  Buyer-selected products
                </strong>

                <p>
                  The backend offers bounded
                  options, but you choose the
                  base product.
                </p>
              </div>
            </div>

            <div className="safety-item">
              <PackageCheck />

              <div>
                <strong>
                  Optional cross-sells
                </strong>

                <p>
                  Add-ons must be trusted,
                  compatible, within limits, and
                  explicitly accepted.
                </p>
              </div>
            </div>

            <div className="safety-item">
              <LockKeyhole />

              <div>
                <strong>
                  Verified payments
                </strong>

                <p>
                  Checkout uses a linked
                  server-stored quote, and the
                  payment signature is verified.
                </p>
              </div>
            </div>

            <div className="boundary-card">
              <span>AI may</span>
              <strong>Understand intent</strong>

              <span>AI may not</span>
              <strong>Control money</strong>
            </div>
            </section>

            <AuditTimeline
              timeline={auditTimeline}
              status={auditStatus}
              error={auditError}
              onRefresh={() =>
                refreshAudit(
                  auditTimeline?.session_id ??
                    activeSession?.sessionId
                )
              }
            />
          </aside>
        </div>
      </section>
    </main>
  );
}

export default App;
