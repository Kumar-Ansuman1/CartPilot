const CHECKOUT_SCRIPT_URL =
  "https://checkout.razorpay.com/v1/checkout.js";

let checkoutScriptPromise = null;


export function loadRazorpayCheckout() {
  if (window.Razorpay) {
    return Promise.resolve();
  }

  if (checkoutScriptPromise) {
    return checkoutScriptPromise;
  }

  checkoutScriptPromise = new Promise(
    (resolve, reject) => {
      const existingScript = document.querySelector(
        `script[src="${CHECKOUT_SCRIPT_URL}"]`
      );

      if (existingScript) {
        existingScript.addEventListener(
          "load",
          resolve,
          { once: true }
        );

        existingScript.addEventListener(
          "error",
          () => {
            reject(
              new Error(
                "Razorpay Checkout could not be loaded."
              )
            );
          },
          { once: true }
        );

        return;
      }

      const script = document.createElement("script");

      script.src = CHECKOUT_SCRIPT_URL;
      script.async = true;

      script.onload = () => resolve();

      script.onerror = () => {
        reject(
          new Error(
            "Razorpay Checkout could not be loaded."
          )
        );
      };

      document.body.appendChild(script);
    }
  );

  return checkoutScriptPromise.catch((error) => {
    checkoutScriptPromise = null;
    throw error;
  });
}


export async function openRazorpayCheckout({
  checkoutOrder,
  productName,
}) {
  await loadRazorpayCheckout();

  return new Promise((resolve, reject) => {
    const checkout = new window.Razorpay({
      key: checkoutOrder.razorpay_key_id,
      order_id: checkoutOrder.razorpay_order_id,
      amount: checkoutOrder.amount_paise,
      currency: checkoutOrder.currency,

      name: "VoltCart",
      description: productName
        ? `Purchase of ${productName}`
        : "Electronics accessory purchase",

      handler: (paymentResponse) => {
        resolve(paymentResponse);
      },

      modal: {
        ondismiss: () => {
          reject(
            new Error(
              "Payment was cancelled before completion."
            )
          );
        },
      },

      theme: {
        color: "#7c3aed",
      },
    });

    checkout.open();
  });
}