// Gustiq AI coach: answers sailors' questions about the wind, current and race course shown on the map.
// Runs as a Cloudflare Pages Function at /api/ask. Needs the secret ANTHROPIC_API_KEY in the Cloudflare project
// (Settings -> Variables and Secrets). The key never reaches the web page.
import Anthropic from "@anthropic-ai/sdk";
import { replies } from "../../cf/common.js";


const SYSTEM = `You are the Gustiq AI sailing coach. You help young sailors (often kids) and their coaches understand
the wind, tidal current and race course on the Gustiq map they are looking at.

You get a JSON summary of what the map shows: the region, the conditions mode (typical wind, a wind the coach set,
or live and forecast), wind and current at the map centre and at each race mark, tide, the course, and simulated
race results if the coach ran them. Use only that information plus general sailing knowledge.

How to answer:
- Short, friendly and clear. Use simple words a 10-year-old can follow; explain any sailing term you use.
- Talk about directions as compass degrees and plain words (for example "from the southwest, 210 degrees").
- Be honest: the map's wind and current between stations are estimates, and boat speeds in the simulator are
  approximations. Say so when it matters, and never present an estimate as a measurement.
- Safety first: if a question touches on dangerous conditions, strong current near rocks or shore, or anything
  about safety on the water, tell them to check with their coach and follow their club's safety rules.
- If the summary does not contain what is needed to answer, say what is missing (for example "put a course
  down first" or "switch to Live & forecast").
- Stay on sailing, wind, weather, tides and the course. Politely decline unrelated requests.`;

export async function onRequest({ request, env }) {
  const { origin, allowed, cors, json } = replies(env, request, "POST, OPTIONS");
  if (request.method === "OPTIONS") return new Response(null, { status: origin && allowed ? 204 : 403, headers: cors });
  if (request.method !== "POST") return json(405, { error: "Use POST." });
  if (!allowed) return json(403, { error: "Not allowed from this website." });
  if (!env.ANTHROPIC_API_KEY) return json(503, { error: "The AI coach is not set up yet: add ANTHROPIC_API_KEY in Cloudflare." });

  let body;
  try { body = await request.json(); } catch { return json(400, { error: "Bad request." }); }
  const msgs = Array.isArray(body.messages) ? body.messages.slice(-10) : [];
  const clean = msgs
    .filter((m) => (m.role === "user" || m.role === "assistant") && typeof m.content === "string" && m.content.trim())
    .map((m) => ({ role: m.role, content: m.content.slice(0, 1500) }));
  while (clean.length && clean[0].role !== "user") clean.shift();
  if (!clean.length || clean[clean.length - 1].role !== "user") return json(400, { error: "Ask a question first." });
  const context = JSON.stringify(body.context || {}).slice(0, 12000);
  // put the map summary with the newest question so the model always sees what is on screen now
  clean[clean.length - 1] = { role: "user", content: `Map summary (JSON):\n${context}\n\nQuestion: ${clean[clean.length - 1].content}` };

  try {
    const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
    const response = await client.beta.messages.create({
      model: "claude-opus-5-5",
      max_tokens: 1500,
      output_config: { effort: "low" },
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      system: SYSTEM,
      messages: clean,
    });
    if (response.stop_reason === "refusal") return json(200, { answer: "I can't help with that one. Try asking about the wind, the current or your course." });
    const text = response.content.filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
    return json(200, { answer: text || "Sorry, I don't have an answer for that." });
  } catch (e) {
    if (e instanceof Anthropic.RateLimitError) return json(429, { error: "The AI coach is busy. Try again in a minute." });
    if (e instanceof Anthropic.AuthenticationError) return json(503, { error: "The AI coach's API key is not valid. Check ANTHROPIC_API_KEY in Cloudflare." });
    if (e instanceof Anthropic.APIConnectionError) return json(502, { error: "Could not reach the AI service. Try again." });
    return json(502, { error: "The AI coach had a problem answering. Try again." });
  }
}
