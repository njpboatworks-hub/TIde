# Publishing Gustiq on Cloudflare Pages

The website (static files in `deploy/sailiq-wind-site`) and the three server parts in `functions/api`
(`ask` = AI coach, `reports` = community reports and added spots, `places` = restaurant ratings) run on
Cloudflare Pages. The GitHub workflow `refresh.yml` publishes every day.

## One-time setup

1. **Cloudflare account.** Sign up at dash.cloudflare.com (the free plan is enough to start).
2. **Account ID.** In the dashboard, open *Workers & Pages*. The Account ID is shown on the right side of the
   overview page (also under *Account home*, the three-dot menu next to the account name, *Copy account ID*).
3. **API token.** *My Profile -> API Tokens -> Create Token -> Custom token*. Permission:
   *Account -> Cloudflare Pages -> Edit*. Account resources: your account. Create it and copy it once.
4. **GitHub secrets.** In the GitHub repository: *Settings -> Secrets and variables -> Actions -> New repository
   secret*. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
5. **First publish.** GitHub *Actions -> Refresh wind data -> Run workflow*. This creates the Pages project
   `gustiq` and publishes the site. Its address is shown in Cloudflare under *Workers & Pages -> gustiq*
   (normally `https://gustiq.pages.dev`; Cloudflare adds a few letters if that name is taken).
6. **Storage for reports (KV).** *Storage & Databases -> KV -> Create namespace*, name it `gustiq-reports`.
   Then *Workers & Pages -> gustiq -> Settings -> Bindings -> Add -> KV namespace*:
   variable name `GUSTIQ_KV`, namespace `gustiq-reports`. Do this for Production.
7. **Secret keys.** *Workers & Pages -> gustiq -> Settings -> Variables and Secrets -> Add*, type *Secret*:
   - `ANTHROPIC_API_KEY` (AI coach)
   - `GOOGLE_PLACES_KEY` (restaurant ratings; optional)
   Optional plain variables: `PLACES_DAILY_CAP` (default 600), `REPORT_SALT` (any random text),
   `ALLOWED_ORIGINS` (comma-separated list of sites allowed to call the functions).
8. **Run the workflow once more** (step 5) so the new bindings and secrets take effect.
9. **iPhone app.** Put the new address in `ios-app/data-host.txt` and run `ios-app/update-app.command`.

## Notes

- Free plan limits change over time; check Cloudflare's current Pages, Workers and KV limits pages.
- KV is eventually consistent: a new report can take up to about a minute to appear everywhere.
- Reports saved earlier on Netlify are not copied over.
