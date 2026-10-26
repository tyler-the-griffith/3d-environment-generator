// JSON API client + server-sent events. Node ids ("project/variant") are URL-encoded.

export const enc = (id) => encodeURIComponent(id);

async function call(method, url, body, raw = false) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    if (raw) { opts.body = body; } else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opts);
  let data = {};
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

export const api = {
  get: (u) => call("GET", u),
  post: (u, b = {}) => call("POST", u, b),
  put: (u, b = {}) => call("PUT", u, b),
  patch: (u, b = {}) => call("PATCH", u, b),
  del: (u) => call("DELETE", u),
  upload: (u, file) => call("PUT", u, file, true),
};

export function events(handlers) {
  let es;
  const connect = () => {
    es = new EventSource("/api/events");
    for (const [name, fn] of Object.entries(handlers)) es.addEventListener(name, (e) => fn(JSON.parse(e.data)));
    es.onerror = () => { es.close(); setTimeout(connect, 2000); };
  };
  connect();
}
