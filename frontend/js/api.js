export class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(method, path, body) {
  let res;
  try {
    res = await fetch("/api" + path, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Cannot reach the Tidyfin server.", 0, "network");
  }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (!res.ok) {
    const msg = data && typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`;
    // Sign-in and wizard endpoints answer 401 for bad credentials; do not treat that as an expired session.
    if (res.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/setup")) onUnauthorized();
    throw new ApiError(msg, res.status, data && data.code);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  put: (p, b) => request("PUT", p, b),
  del: (p) => request("DELETE", p),
  /** POST that returns a Blob (image). */
  async blob(p, b) {
    const res = await fetch("/api" + p, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
    return res.blob();
  },
};

export const enc = encodeURIComponent;
