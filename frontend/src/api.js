const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ??
  "http://127.0.0.1:8000"
).replace(/\/+$/, "");


async function apiRequest(path, options = {}) {
  let response;

  try {
    response = await fetch(
      `${API_BASE_URL}${path}`,
      {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(options.headers ?? {}),
        },
      }
    );
  } catch {
    throw new Error(
      "Could not reach the CartPilot API. " +
        "Is the backend running?"
    );
  }

  const data = await response
    .json()
    .catch(() => null);

  if (!response.ok) {
    let message =
      "The request could not be completed.";

    if (typeof data?.detail === "string") {
      message = data.detail;
    } else if (Array.isArray(data?.detail)) {
      message =
        data.detail[0]?.msg ??
        "Some submitted information is invalid.";
    }

    throw new Error(message);
  }

  return data;
}


export function startShoppingSession(message) {
  return apiRequest("/api/shop", {
    method: "POST",
    body: JSON.stringify({
      message,
    }),
  });
}


export function getShoppingAudit(sessionId) {
  return apiRequest(
    `/api/shop/${encodeURIComponent(sessionId)}/audit`,
    {
      method: "GET",
    }
  );
}


export function getPaymentStatus(quoteId) {
  return apiRequest(
    `/api/payment/status/${encodeURIComponent(quoteId)}`,
    {
      method: "GET",
    }
  );
}


export function selectBaseProduct(
  sessionId,
  baseProductSku
) {
  return apiRequest("/api/shop/select-base", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      base_product_sku: baseProductSku,
    }),
  });
}


function submitCrossSellDecision(
  sessionId,
  decision,
  crossSellProductSku
) {
  return apiRequest(
    "/api/shop/select-cross-sell",
    {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        decision,
        cross_sell_product_sku:
          crossSellProductSku,
      }),
    }
  );
}


export function acceptCrossSell(
  sessionId,
  crossSellProductSku
) {
  return submitCrossSellDecision(
    sessionId,
    "accept",
    crossSellProductSku
  );
}


export function declineCrossSell(sessionId) {
  return submitCrossSellDecision(
    sessionId,
    "decline",
    null
  );
}


export function confirmCheckout(quoteId) {
  return apiRequest(
    "/api/checkout/confirm",
    {
      method: "POST",
      body: JSON.stringify({
        quote_id: quoteId,
        confirmed: true,
      }),
    }
  );
}


export function createPurchaseMandate({
  budgetPaise,
  allowedCategories,
  requiredCompatibility,
  maxCrossSellPercentage,
  expiresInMinutes,
  buyerGoal,
}) {
  return apiRequest("/api/mandates", {
    method: "POST",
    body: JSON.stringify({
      budget_paise: budgetPaise,
      allowed_categories: allowedCategories,
      required_compatibility: requiredCompatibility,
      max_cross_sell_percentage: maxCrossSellPercentage,
      expires_in_minutes: expiresInMinutes,
      checkout_confirmation_required: true,
      buyer_goal: buyerGoal,
    }),
  });
}


export function getPurchaseMandate(mandateId) {
  return apiRequest(
    `/api/mandates/${encodeURIComponent(mandateId)}`,
    { method: "GET" }
  );
}


export function getMandateAudit(mandateId) {
  return apiRequest(
    `/api/mandates/${encodeURIComponent(mandateId)}/audit`,
    { method: "GET" }
  );
}


export function runDelegatedShop(mandateId, task) {
  return apiRequest("/api/delegated-shop", {
    method: "POST",
    body: JSON.stringify({
      mandate_id: mandateId,
      task,
    }),
  });
}


export function getDelegatedExecution(executionId) {
  return apiRequest(
    `/api/agent/executions/${encodeURIComponent(executionId)}`,
    { method: "GET" }
  );
}


export function confirmDelegatedCheckout(quoteId) {
  return apiRequest(
    "/api/delegated-checkout/confirm",
    {
      method: "POST",
      body: JSON.stringify({
        quote_id: quoteId,
        confirmed: true,
      }),
    }
  );
}


export function verifyPayment(
  quoteId,
  razorpayResponse
) {
  return apiRequest(
    "/api/payment/verify",
    {
      method: "POST",
      body: JSON.stringify({
        quote_id: quoteId,
        razorpay_order_id:
          razorpayResponse.razorpay_order_id,
        razorpay_payment_id:
          razorpayResponse.razorpay_payment_id,
        razorpay_signature:
          razorpayResponse.razorpay_signature,
      }),
    }
  );
}
