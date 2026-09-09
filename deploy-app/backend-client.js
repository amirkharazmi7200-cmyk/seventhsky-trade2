(() => {
  "use strict";

  const BASE = "api-proxy.php";

  const messages = {
    app_proxy_not_configured: "تنظیمات اتصال اپ به سرور کامل نشده است.",
    app_proxy_token_missing: "کلید واقعی اتصال هنوز در تنظیمات هاست وارد نشده است.",
    unauthorized: "سرور کلید اتصال اپ را نپذیرفت؛ تنظیمات کلید و ارسال آن باید بررسی شود.",
    backend_not_configured: "تنظیمات سرور داده‌ها کامل نشده است.",
    backend_proxy_failed: "ارتباط هاست اپ با سرور داده‌ها برقرار نشد.",
    proxy_transport_unavailable: "امکان اتصال به سرور داده‌ها روی هاست فعال نیست.",
    invalid_response: "پاسخ معتبر از سرور داده‌ها دریافت نشد.",
    network_error: "ارتباط با سرور قطع شد؛ اتصال اینترنت را بررسی کنید.",
  };

  function apiError(code) {
    const error = new Error(messages[code] || "دریافت یا ذخیرهٔ اطلاعات انجام نشد؛ دوباره تلاش کنید.");
    error.code = code;
    return error;
  }

  async function request(resource, { method = "GET", query = {}, body } = {}) {
    const url = new URL(BASE, location.href);
    url.searchParams.set("resource", resource);
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });

    let response;
    try {
      response = await fetch(url, {
        method,
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (_) {
      throw apiError("network_error");
    }

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      throw apiError(response.status === 401 ? "unauthorized" : "invalid_response");
    }

    if (!response.ok || !data || data.ok !== true) {
      throw apiError(data?.error || (response.status === 401 ? "unauthorized" : "invalid_response"));
    }
    return data;
  }

  window.SevenSkyAPI = {
    request,
    health: () => request("health"),
    leads: async () => (await request("leads")).leads || [],
    promoteLeads: (leads) => request("bootstrap", { method: "POST", body: { leads } }),
    state: async (id) => (await request("state", { query: { lead_id: id } })).state,
    saveState: (id, state) => request("state", { method: "PUT", query: { lead_id: id }, body: state }),
    activities: async (id) =>
      (await request("activities", { query: { lead_id: id } })).activities || [],
    addActivity: (id, kind, data) =>
      request("activities", { method: "POST", query: { lead_id: id }, body: { kind, data } }),
    inbox: async (limit = 100, source = "") =>
      (await request("inbox", { query: { limit, source } })).events || [],
  };
})();
