# Daily automatic updates

GitHub runs `.github/workflows/refresh.yml` every morning. It downloads new data and publishes the site to Cloudflare Pages.

## One-time setup
See `deploy/CLOUDFLARE.md` (Cloudflare account, API token, two GitHub secrets, KV storage, keys).

**Run it once by hand:** repository > **Actions** > **Refresh wind data** > **Run workflow**.
   It takes about 20 to 30 minutes (the worldwide wind, weather and ocean-current forecast is the slow part).
   A green check means your site is updated.

The Wind Map and Forecast tabs need this daily run; without it their forecast stops at the day the folder was built.

## Changing the schedule
Edit the `cron` line in `.github/workflows/refresh.yml`. Times are UTC.
`"23 10 * * *"` = once a day; `"23 */6 * * *"` = every 6 hours.

## If a run fails
GitHub emails you. Open the run in the **Actions** tab to see which step failed. The usual causes are an
expired Cloudflare API token or a data service being temporarily down (the next run usually works).

Your Mapbox key (if you add one) goes in `scripts/build_deploy.py` (the `mapboxToken` line in CONFIG),
because the website folder is rebuilt on every run.

## AI coach (Race Planning tab)
The "Ask the AI coach" box calls a small server function (`functions/api/ask.js`) that asks Claude
(Anthropic's AI). It needs your Anthropic API key stored in Cloudflare:

1. Create an API key at console.anthropic.com (Settings → API keys). Add a payment method there; each
   question is billed to that account. You can set a monthly spend limit in the same console.
2. In Cloudflare: **Workers & Pages → gustiq → Settings → Variables and Secrets → Add**, type Secret.
   Name: `ANTHROPIC_API_KEY`, value: the key. Never put the key in GitHub or in the website files.
3. Run the workflow once (Actions → Refresh wind data → Run workflow). The Race Planning tab's chat then answers.

The functions only accept requests from njpboatworks.com, gustiq.app, the gustiq*.pages.dev address and the
iPhone app. To allow another domain, add a Cloudflare variable `ALLOWED_ORIGINS` with a comma-separated list.
