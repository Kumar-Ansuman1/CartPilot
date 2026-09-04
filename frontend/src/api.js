const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ??
  "http://127.0.0.1:8000";


async function apiRequest(path, options = {}) {
  let response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      ...options,
    });
  } catch {
    throw new Error(
      "Could not reach the CartPilot API. Is the backend running?"
    );
  }

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    let message = "The request could not be completed.";

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


export function requestShoppingQuote(message) {
  return apiRequest("/api/shop", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}


export function confirmCheckout(quoteId) {
  return apiRequest("/api/checkout/confirm", {
    method: "POST",
    body: JSON.stringify({
      quote_id: quoteId,
      confirmed: true,
    }),
  });
}


export function verifyPayment(
  quoteId,
  razorpayResponse
) {
  return apiRequest("/api/payment/verify", {
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
  });
}