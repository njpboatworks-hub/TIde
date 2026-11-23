// Restaurants and bars near a harbor for the Cruising tab, from the Google Places API (New).
// Runs as a Cloudflare Pages Function at /api/places. Needs the secret GOOGLE_PLACES_KEY in the Cloudflare project
// (Settings -> Variables and Secrets). The key never reaches the web page. A daily cap (PLACES_DAILY_CAP, default
// 600 calls, kept low because the free Cloudflare KV plan allows a limited number of saves a day) protects the
// Google bill. The count is kept in the KV binding GUSTIQ_KV.
//
//   GET /api/places?lat=41.49&lon=-71.31   -> { places: [...] }   (Nearby Search, up to 20)
//   GET /api/places?id=ChIJ...             -> { place: {..., reviews} } (Place Details with reviews)
//
// Google's terms require showing Google attribution with this data and the review authors' names; the page does.
// Responses are not stored on the server; browsers may keep them for 10 minutes.
import { replies } from "../../cf/common.js";

const API = "https://places.googleapis.com/v1";
const LIST_FIELDS = ["id", "displayName", "location", "rating", "userRatingCount", "priceLevel", "primaryTypeDisplayName",
  "googleMapsUri", "currentOpeningHours.openNow", "formattedAddress"].map((f) => `places.${f}`).join(",");
const DETAIL_FIELDS = "id,displayName,location,rating,userRatingCount,priceLevel,primaryTypeDisplayName,googleMapsUri,websiteUri," +
  "nationalPhoneNumber,formattedAddress,currentOpeningHours.openNow,currentOpeningHours.weekdayDescriptions,reviews";

function slim(p) {
  return {
    id: p.id, name: p.displayName?.text || "", lat: p.location?.latitude, lon: p.location?.longitude, rating: p.rating ?? null,
    count: p.userRatingCount ?? 0, price: p.priceLevel || "", kind: p.primaryTypeDisplayName?.text || "", url: p.googleMapsUri || "",
    open: p.currentOpeningHours?.openNow ?? null, address: p.formattedAddress || "", web: p.websiteUri || "", phone: p.nationalPhoneNumber || "",
    hours: p.currentOpeningHours?.weekdayDescriptions || [],
    reviews: (p.reviews || []).slice(0, 5).map((r) => ({ rating: r.rating, text: (r.text?.text || r.originalText?.text || "").slice(0, 600),
      when: r.relativePublishTimeDescription || "", author: r.authorAttribution?.displayName || "", authorUrl: r.authorAttribution?.uri || "" })),
  };
}

export async function onRequest({ request, env }) {
  const { origin, allowed, cors, json } = replies(env, request, "GET, OPTIONS");
  if (request.method === "OPTIONS") return new Response(null, { status: origin && allowed ? 204 : 403, headers: cors });
  if (request.method !== "GET") return json(405, { error: "Use GET." });
  if (!allowed) return json(403, { error: "Not allowed from this website." });
  const key = env.GOOGLE_PLACES_KEY;
  if (!key) return json(503, { error: "Restaurant listings are not set up yet: add GOOGLE_PLACES_KEY in Cloudflare." });

  // daily cap on Google calls
  const kv = env.GUSTIQ_KV;
  if (!kv) return json(503, { error: "Restaurant listings are not set up yet: add the KV binding GUSTIQ_KV in Cloudflare." });
  const cap = +(env.PLACES_DAILY_CAP || 600), day = `places/${new Date().toISOString().slice(0, 10)}`;
  const used = (await kv.get(day, { type: "json" }).catch(() => 0)) || 0;
  if (used >= cap) return json(429, { error: "Restaurant listings have reached today's limit. They will be back tomorrow." });
  await kv.put(day, JSON.stringify(used + 1), { expirationTtl: 172800 }).catch(() => {});

  const url = new URL(request.url), id = url.searchParams.get("id");
  try {
    if (id) {
      if (!/^[A-Za-z0-9_-]{10,300}$/.test(id)) return json(400, { error: "Bad place." });
      const r = await fetch(`${API}/places/${id}`, { headers: { "X-Goog-Api-Key": key, "X-Goog-FieldMask": DETAIL_FIELDS } });
      const d = await r.json();
      if (!r.ok) return json(502, { error: "Google did not answer.", detail: d?.error?.status || r.status });
      return json(200, { place: slim(d) }, { "Cache-Control": "private, max-age=600" });
    }
    const lat = +url.searchParams.get("lat"), lon = +url.searchParams.get("lon");
    if (!(lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180)) return json(400, { error: "Bad position." });
    const r = await fetch(`${API}/places:searchNearby`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": LIST_FIELDS },
      body: JSON.stringify({ includedTypes: ["restaurant", "bar"], maxResultCount: 20, rankPreference: "POPULARITY",
        locationRestriction: { circle: { center: { latitude: lat, longitude: lon }, radius: 2500 } } }),
    });
    const d = await r.json();
    if (!r.ok) return json(502, { error: "Google did not answer.", detail: d?.error?.status || r.status });
    return json(200, { places: (d.places || []).map(slim) }, { "Cache-Control": "private, max-age=600" });
  } catch (e) {
    return json(502, { error: "Could not reach Google right now." });
  }
}
