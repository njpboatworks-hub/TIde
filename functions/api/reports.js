// Nockd community reports for the Cruising tab: "I stayed here" (with the date), "it's gone", short notes, and
// spots people add themselves. Anonymous. Runs as a Cloudflare Pages Function at /api/reports and stores data in
// Cloudflare KV (binding name NOCKD_KV, set in the Cloudflare project under Settings -> Bindings).
//
//   GET  /api/reports              -> { summary: { key: { last, n, gone, lastNote } }, spots: [...] }
//   GET  /api/reports?key=K        -> { reports: [...] } for one place (newest first)
//   POST { action: "report", key, kind: "stayed"|"gone"|"note", date, note }
//   POST { action: "spot", type, lat, lon, name, note }
//
// Spam limits: 20 posts a day per visitor (IP address, stored only as a salted hash for 2 days), 280-character
// notes, no links.
// KV note: KV has no "save only if unchanged", so two posts to the same place in the same second could in rare
// cases lose one. KV is also eventually consistent: a new report can take up to about a minute to show everywhere.
import { replies, sha256hex, randomHex } from "../../cf/common.js";

const KINDS = new Set(["stayed", "gone", "note"]);
const SPOT_TYPES = new Set(["mooring", "mooring_field", "anchorage", "dinghy", "pumpout", "fuel", "ramp", "fishing", "other"]);
const DAY_LIMIT = 20;

const clean = (s, n) => String(s ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, n);
const hasLink = (s) => /(https?:\/\/|www\.|\.(com|net|org|io|co)\b)/i.test(s);
const validKey = (k) => typeof k === "string" && /^[a-z_]{2,16}:-?\d{1,3}\.\d{3,5},-?\d{1,3}\.\d{3,5}$|^u:[a-f0-9]{12}$/.test(k);

async function update(kv, key, fn, empty) {
  const cur = await kv.get(key, { type: "json" });
  const next = fn(cur ?? structuredClone(empty));
  await kv.put(key, JSON.stringify(next));
  return next;
}

export async function onRequest({ request, env }) {
  const { origin, allowed, cors, json } = replies(env, request, "GET, POST, OPTIONS");
  if (request.method === "OPTIONS") return new Response(null, { status: origin && allowed ? 204 : 403, headers: cors });
  if (!allowed) return json(403, { error: "Not allowed from this website." });
  const kv = env.NOCKD_KV;
  if (!kv) return json(503, { error: "Reports are not set up yet: add the KV binding NOCKD_KV in Cloudflare." });
  const url = new URL(request.url);

  if (request.method === "GET") {
    const key = url.searchParams.get("key");
    if (key) {
      if (!validKey(key)) return json(400, { error: "Bad place." });
      const r = await kv.get(`p/${key}`, { type: "json" });
      return json(200, { reports: (r || []).slice().reverse() }, { "Cache-Control": "public, max-age=30" });
    }
    const [summary, spots] = await Promise.all([kv.get("summary", { type: "json" }), kv.get("spots", { type: "json" })]);
    return json(200, { summary: summary || {}, spots: spots || [] }, { "Cache-Control": "public, max-age=60" });
  }
  if (request.method !== "POST") return json(405, { error: "Use GET or POST." });

  let b; try { b = await request.json(); } catch { return json(400, { error: "Bad request." }); }
  // daily limit per visitor; the IP address is only kept as a salted hash
  const ip = request.headers.get("cf-connecting-ip") || "?";
  const day = new Date().toISOString().slice(0, 10);
  const who = (await sha256hex(`${env.REPORT_SALT || "nockd"}|${day}|${ip}`)).slice(0, 16);
  const rlKey = `rl/${day}/${who}`, used = (await kv.get(rlKey, { type: "json" })) || 0;
  if (used >= DAY_LIMIT) return json(429, { error: "That's a lot of reports for one day. Try again tomorrow." });
  const countPost = () => kv.put(rlKey, JSON.stringify(used + 1), { expirationTtl: 172800 });

  const note = clean(b.note, 280);
  if (hasLink(note)) return json(400, { error: "Please leave links out of notes." });

  try {
    if (b.action === "report") {
      if (!validKey(b.key) || !KINDS.has(b.kind)) return json(400, { error: "Bad report." });
      const date = /^\d{4}-\d{2}-\d{2}$/.test(b.date || "") ? b.date : day;
      if (date > day || date < "2000-01-01") return json(400, { error: "Pick a date that is not in the future." });
      const rec = { t: new Date().toISOString(), kind: b.kind, date, note };
      await update(kv, `p/${b.key}`, (list) => [...list, rec].slice(-50), []);
      await update(kv, "summary", (s) => {
        const e = s[b.key] || { n: 0 };
        e.n += 1;
        if (b.kind === "stayed") { if (!e.last || date > e.last) e.last = date; e.gone = false; }
        if (b.kind === "gone") { e.gone = true; e.goneOn = date; }
        if (note) e.lastNote = note.slice(0, 120);
        s[b.key] = e; return s;
      }, {});
      await countPost();
      return json(200, { ok: true });
    }
    if (b.action === "spot") {
      const lat = +b.lat, lon = +b.lon, name = clean(b.name, 60);
      if (!SPOT_TYPES.has(b.type) || !(lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180)) return json(400, { error: "Bad spot." });
      if (hasLink(name)) return json(400, { error: "Please leave links out of names." });
      const spot = { key: `u:${randomHex(6)}`, type: b.type, lat: Math.round(lat * 1e5) / 1e5, lon: Math.round(lon * 1e5) / 1e5, name, note, t: new Date().toISOString() };
      await update(kv, "spots", (list) => [...list, spot].slice(-5000), []);
      await countPost();
      return json(200, { ok: true, spot });
    }
    return json(400, { error: "Unknown action." });
  } catch (e) {
    return json(503, { error: "Could not save right now. Try again in a minute." });
  }
}
