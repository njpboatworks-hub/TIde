# Put the Nockd Lift, Header and Tide Guide on your own domain

This folder is the whole website: plain files, no server code. Any static host works.
The steps below use **Netlify** (free plan) and a domain registered at **GoDaddy**.

## 1. Put the site online (about 5 minutes)
1. Make a free account at netlify.com.
2. Go to **Sites → Add new site → Deploy manually** (Netlify Drop).
3. Drag this whole `sailiq-wind-site` folder onto the page.
4. Netlify gives you an address like `something.netlify.app`. Open it and check the map works.
   You can rename it under **Site configuration → Change site name**.

To update the site later, drag the new folder onto **Deploys** for the same site.

## 2. Connect your domain (GoDaddy)
1. In Netlify: **Domain management → Add a domain**, type your domain (for example `yourdomain.com`),
   and choose to keep your DNS at GoDaddy (external DNS). Netlify then shows the exact records to add.
2. In GoDaddy: **My Products → your domain → DNS → Manage DNS**, and add what Netlify showed you. Usually:
   - **CNAME** record: Name `www`, Value `something.netlify.app`
   - **A** record: Name `@`, Value = the load balancer IP shown in Netlify's domain settings
     (Netlify's documentation lists `75.2.60.5`; use whatever your dashboard shows).
   - Delete any existing GoDaddy **A** record for `@` that points to "Parked" or a GoDaddy site,
     and any domain *forwarding* you set up for this domain.
   - Do not add an AAAA (IPv6) record for `@`.
3. Wait for DNS to update (often minutes, up to 48 hours). Netlify then issues the free HTTPS
   certificate automatically. Netlify recommends `www.yourdomain.com` as the main address.

## Satellite map
The site opens on satellite imagery: **USGS The National Map** over the US (public domain, sharpest up close) and
**Sentinel-2 cloudless 2016 by EOX** everywhere else, including Spain and Portugal (CC BY 4.0, credited on the map;
about 10 m detail, so it softens when zoomed in very close). No key or account is needed for either. Visitors can switch between **Satellite** and **Drawn** at the top right of the map.

## 3. Add Mapbox maps (optional)
1. Make an account at mapbox.com and copy a **public** access token (starts with `pk.`).
2. In the token's settings, add **URL restrictions** for your domain (and the netlify.app address),
   so nobody else can use your token.
3. Open `config.js` in this folder, paste the token between the quotes, save, and redeploy.
   Mapbox satellite, Streets and Outdoors then appear as extra choices on the map switch.

Check Mapbox's current pricing and its attribution rules for your plan before launch. The map shows
"© Mapbox © OpenStreetMap / Improve this map" credits; Mapbox may also require its logo.

## Data freshness
The readings, the 7-day forecast and the tide and current predictions are refreshed every morning by the
GitHub workflow in the project (see AUTO_UPDATE.md), which republishes this folder to Netlify. A folder you
drop in by hand holds the data from the day it was built until the next automatic refresh.

## Regions and water depth
US East & Gulf, US West Coast, Great Lakes (no tides there), Hawaii, and Spain & Portugal. Current arrows between
NOAA stations are adjusted for water depth from NOAA NCEI depth models (CUDEM 1/9 and 1/3 arc-second where NCEI
publishes them, the 3 arc-second Coastal Relief Model elsewhere), in the `depth` folder.

Sources: NOAA NDBC, CO-OPS and NCEI (depth), National Weather Service, MET Norway, Iowa Environmental Mesonet, US Census Bureau,
GSHHG shoreline (LGPL), and OpenStreetMap contributors. Not for navigation.
