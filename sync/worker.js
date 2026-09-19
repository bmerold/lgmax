/*
 * LeafGreen Maximizer — cross-device progress sync.
 *
 * A single anonymous "sync key" is the whole identity: no login, no email, no
 * personal data. The key names a row in KV that holds one JSON progress bundle
 * (the checked-off stops, the play position, starter + trading choice). Enter
 * the same key on another device and both stay in step.
 *
 * GET  /?key=KEY        -> the stored bundle, or {} if the key is new
 * PUT  /?key=KEY  <json -> merge the sent bundle into the stored one, store it,
 *                          and return the merged result so the caller converges
 *
 * The PUT does a read-merge-write so two devices saving at once cannot clobber
 * each other: the done-list is unioned (progress only ever grows), and the
 * play position / starter / trading are taken from whichever bundle carries the
 * newer timestamp. That keeps the store monotonic without any locking.
 */

// Only these origins may call the worker from a browser. localhost is for
// testing the built app.html straight off disk / a dev server.
const ALLOWED_ORIGINS = new Set([
  "https://lgmax.arcane-collectibles.com",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
]);

const MAX_BODY_BYTES = 256 * 1024; // a full done-list is a few thousand short strings
const KEY_RE = /^[A-Za-z0-9_-]{8,64}$/;

function corsHeaders(origin) {
  const h = {
    "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
  if (origin && ALLOWED_ORIGINS.has(origin)) h["Access-Control-Allow-Origin"] = origin;
  return h;
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(origin) },
  });
}

// Fold the incoming bundle into what is already stored: union the checked
// stops, keep the newer play position / choices.
function merge(stored, incoming) {
  const s = stored || {};
  const done = new Set([...(s.done || []), ...(incoming.done || [])]);
  const newer = (incoming.ts || 0) >= (s.ts || 0) ? incoming : s;
  return {
    v: 1,
    done: [...done],
    play: newer.play || s.play || null,
    starter: newer.starter || s.starter || null,
    trading: typeof newer.trading === "boolean" ? newer.trading : !!s.trading,
    ts: Math.max(s.ts || 0, incoming.ts || 0),
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin");
    const url = new URL(request.url);
    const key = url.searchParams.get("key");

    if (request.method === "OPTIONS")
      return new Response(null, { status: 204, headers: corsHeaders(origin) });

    // A browser request from an unlisted origin gets no CORS grant, so the
    // response is unreadable to the page — reject it outright to be explicit.
    if (origin && !ALLOWED_ORIGINS.has(origin))
      return json({ error: "origin not allowed" }, 403, origin);

    if (!key || !KEY_RE.test(key))
      return json({ error: "bad or missing key" }, 400, origin);

    if (request.method === "GET") {
      const stored = await env.LGMAX.get(key);
      return json(stored ? JSON.parse(stored) : {}, 200, origin);
    }

    if (request.method === "PUT") {
      const raw = await request.text();
      if (raw.length > MAX_BODY_BYTES)
        return json({ error: "payload too large" }, 413, origin);
      let incoming;
      try {
        incoming = JSON.parse(raw || "{}");
      } catch (e) {
        return json({ error: "invalid json" }, 400, origin);
      }
      const stored = await env.LGMAX.get(key);
      const merged = merge(stored ? JSON.parse(stored) : null, incoming);
      await env.LGMAX.put(key, JSON.stringify(merged));
      return json(merged, 200, origin);
    }

    return json({ error: "method not allowed" }, 405, origin);
  },
};
