(() => {
  "use strict";

  const BASE = "api-proxy.php";

  async function request(resource, { method = "GET", query = {}, body } = {}) {
    const url = new URL(BASE, location.href);
    url.searchParams.set("resource", resource);
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });

    const response = await fetch(url, {
      method,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }

    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
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
