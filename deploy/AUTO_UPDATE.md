# Daily automatic updates

GitHub runs `.github/workflows/refresh.yml` every morning. It downloads new data and publishes the site to Netlify.

## One-time setup
1. **Netlify personal access token:** Netlify > your avatar > **User settings** > **Applications** >
   **Personal access tokens** > **New access token**. Copy it.
2. **Netlify site ID:** your site > **Site configuration** > **Site details** > copy **Site ID** (also called API ID).
3. **Add both to GitHub:** your repository > **Settings** > **Secrets and variables** > **Actions** >
   **New repository secret**:
   - Name `NETLIFY_AUTH_TOKEN`, value = the token
   - Name `NETLIFY_SITE_ID`, value = the site ID
4. **Run it once by hand:** repository > **Actions** > **Refresh wind data** > **Run workflow**.
   It takes a few minutes. A green check means your site is updated.

## Changing the schedule
Edit the `cron` line in `.github/workflows/refresh.yml`. Times are UTC.
`"23 10 * * *"` = once a day; `"23 */6 * * *"` = every 6 hours.

## If a run fails
GitHub emails you. Open the run in the **Actions** tab to see which step failed. The usual causes are an
expired Netlify token or a data service being temporarily down (the next run usually works).

Your Mapbox key (if you add one) goes in `scripts/build_deploy.py` (the `mapboxToken` line in CONFIG),
because the website folder is rebuilt on every run.

## AI coach (Race Planning tab)
The "Ask the AI coach" box calls a small server function (`netlify/functions/ask.mjs`) that asks Claude
(Anthropic's AI). It only works when the site is published by this GitHub workflow (drag-and-drop uploads
cannot run server code), and it needs your Anthropic API key stored in Netlify:

1. Create an API key at console.anthropic.com (Settings → API keys). Add a payment method there; each
   question is billed to that account. You can set a monthly spend limit in the same console.
2. In Netlify: your site → **Site configuration → Environment variables → Add a variable**.
   Key: `ANTHROPIC_API_KEY`, value: the key. Never put the key in GitHub or in the website files.
3. Run the workflow once (Actions → Refresh wind data → Run workflow). The Race Planning tab's chat then answers.

The function only accepts requests from njpboatworks.com, www.njpboatworks.com and *.netlify.app pages.
To allow another domain, add a Netlify environment variable `ALLOWED_ORIGINS` with a comma-separated list.
