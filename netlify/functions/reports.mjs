// Gustiq community reports for the Cruising tab: "I stayed here" (with the date), "it's gone", short notes, and
// spots people add themselves. Anonymous; stored with Netlify Blobs on the same site (no extra service or key).
//
//   GET  /.netlify/functions/reports              -> { summary: { key: { last, n, gone, lastNote } }, spots: [...] }
//   GET  /.netlify/functions/reports?key=K        -> { reports: [...] } for one place (newest first)
//   POST { action: "report", key, kind: "stayed"|"gone"|"note", date, note }
//   POST { action: "spot", type, lat, lon, name, note }
//
// Spam limits: 20 posts a day per visitor (IP address, stored only as a salted hash), 280-character notes, no links.
import { getStore } from "@netlify/blobs";
import crypto from "node:crypto";

const ALLOWED = (process.env.ALLOWED_ORIGINS || "https://njpboatworks.com,https://www.njpboatworks.com,capacitor://localhost")
  .split(",").map((s) => s.trim()).filter(Boolean);
const okOrigin = (o) => ALLOWED.includes(o) || o.endsWith(".netlify.app");
const KINDS = new Set(["stayed", "gone", "note"]);
const SPOT_TYPES = new Set(["mooring", "mooring_field", "anchorage", "dinghy", "pumpout", "fuel", "ramp", "fishing", "other"]);
const DAY_LIMIT = 20;

const clean = (s, n) => String(s ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, n);
const hasLink = (s) => /(https?:\/\/|www\.|\.(com|net|org|io|co)\b)/i.test(s);
const validKey = (k) => typeof k === "string" && /^[a-z_]{2,16}:-?\d{1,3}\.\d{3,5},-?\d{1,3}\.\d{3,5}$|^u:[a-f0-9]{12}$/.test(k);

// read-modify-write with the blob's etag, retried, so two people posting at once do not overwrite each other
async function update(store, key, fn, empty) {
  for (let i = 0; i < 12; i++) {
    const cur = await store.getWithMetadata(key, { type: "json" });
    const next = fn(cur ? cur.data : structuredClone(empty));
    const res = cur ? await store.setJSON(key, next, { onlyIfMatch: cur.etag }) : await store.setJSON(key, next, { onlyIfNew: true });
    if (res.modified !== false) return next;
    await new Promise((r) => setTimeout(r, 20 + Math.random() * 80 * (i + 1))); // someone else saved first: wait a moment, re-read, retry
  }
  throw new Error("busy");
}

export default async (req) => {
  const origin = req.headers.get("origin") || "";
  const cors = origin && okOrigin(origin) ? { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", Vary: "Origin" } : {};
  const json = (status, body, extra = {}) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...cors, ...extra } });
  if (req.method === "OPTIONS") return new Response(null, { status: origin && okOrigin(origin) ? 204 : 403, headers: cors });
  if (origin && !okOrigin(origin)) return json(403, { error: "Not allowed from this website." });

  const store = getStore("cruising-reports");
  const url = new URL(req.url);

  if (req.method === "GET") {
    const key = url.searchParams.get("key");
    if (key) {
      if (!validKey(key)) return json(400, { error: "Bad place." });
      const r = await store.get(`p/${key}`, { type: "json" });
      return json(200, { reports: (r || []).slice().reverse() }, { "Cache-Control": "public, max-age=30" });
    }
    const [summary, spots] = await Promise.all([store.get("summary", { type: "json" }), store.get("spots", { type: "json" })]);
    return json(200, { summary: summary || {}, spots: spots || [] }, { "Cache-Control": "public, max-age=60" });
  }
  if (req.method !== "POST") return json(405, { error: "Use GET or POST." });

  let b; try { b = await req.json(); } catch { return json(400, { error: "Bad request." }); }
  // daily limit per visitor; the IP address is only kept as a salted hash for one day
  const ip = req.headers.get("x-nf-client-connection-ip") || req.headers.get("x-forwarded-for") || "?";
  const day = new Date().toISOString().slice(0, 10);
  const who = crypto.createHash("sha256").update(`${process.env.REPORT_SALT || "nockd"}|${day}|${ip}`).digest("hex").slice(0, 16);
  const rlKey = `rl/${day}/${who}`, used = (await store.get(rlKey, { type: "json" })) || 0;
  if (used >= DAY_LIMIT) return json(429, { error: "That's a lot of reports for one day. Try again tomorrow." });

  const note = clean(b.note, 280);
  if (hasLink(note)) return json(400, { error: "Please leave links out of notes." });

  try {
    if (b.action === "report") {
      if (!validKey(b.key) || !KINDS.has(b.kind)) return json(400, { error: "Bad report." });
      const date = /^\d{4}-\d{2}-\d{2}$/.test(b.date || "") ? b.date : day;
      if (date > day || date < "2000-01-01") return json(400, { error: "Pick a date that is not in the future." });
      const rec = { t: new Date().toISOString(), kind: b.kind, date, note };
      await update(store, `p/${b.key}`, (list) => [...list, rec].slice(-50), []);
      await update(store, "summary", (s) => {
        const e = s[b.key] || { n: 0 };
        e.n += 1;
        if (b.kind === "stayed") { if (!e.last || date > e.last) e.last = date; e.gone = false; }
        if (b.kind === "gone") { e.gone = true; e.goneOn = date; }
        if (note) e.lastNote = note.slice(0, 120);
        s[b.key] = e; return s;
      }, {});
    } else if (b.action === "spot") {
      const lat = +b.lat, lon = +b.lon, name = clean(b.name, 60);
      if (!SPOT_TYPES.has(b.type) || !(lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180)) return json(400, { error: "Bad spot." });
      if (hasLink(name)) return json(400, { error: "Please leave links out of names." });
      const id = crypto.randomBytes(6).toString("hex");
      const spot = { key: `u:${id}`, type: b.type, lat: Math.round(lat * 1e5) / 1e5, lon: Math.round(lon * 1e5) / 1e5, name, note, t: new Date().toISOString() };
      await update(store, "spots", (list) => [...list, spot].slice(-5000), []);
      await store.setJSON(rlKey, used + 1);
      return json(200, { ok: true, spot });
    } else return json(400, { error: "Unknown action." });
  } catch (e) {
    return json(503, { error: "Could not save right now. Try again in a minute." });
  }
  await store.setJSON(rlKey, used + 1);
  return json(200, { ok: true });
};
