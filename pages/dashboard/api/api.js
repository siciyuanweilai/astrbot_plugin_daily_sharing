export function withTimeout(promise, timeoutMs, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => window.clearTimeout(timeoutId));
}

export function normalizeBridgeResult(result) {
  if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "ok")) {
    if (!result.ok) {
      throw new Error(result.error?.message || result.message || "请求失败");
    }
    return result.data || {};
  }
  return result || {};
}

export function createDashboardApi(bridge, requestTimeoutMs) {
  async function apiGet(endpoint, params = {}, timeoutMs = requestTimeoutMs) {
    const result = await withTimeout(
      bridge.apiGet(endpoint, params),
      timeoutMs,
      "请求超时"
    );
    return normalizeBridgeResult(result);
  }

  async function apiPost(endpoint, body = {}, timeoutMs = requestTimeoutMs) {
    const result = await withTimeout(
      bridge.apiPost(endpoint, body),
      timeoutMs,
      "请求超时"
    );
    return normalizeBridgeResult(result);
  }

  return { apiGet, apiPost };
}
