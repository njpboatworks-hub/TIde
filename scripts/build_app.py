"""Build the web part of the Gustiq iPhone app (ios-app/www).

The app contains only the page itself (HTML, script, logo and settings, well under 1 MB). The map data (forecasts,
tides, currents, the worldwide grids; about 250 MB that changes every few hours) is loaded from the website, so the app
always shows the same fresh data as the site and does not need an App Store update when the data changes.

Usage:
  python -m scripts.build_app --data https://your-site.netlify.app/
The address is saved in ios-app/data-host.txt, so later builds can leave out --data.
It must be the real Netlify address (or a domain pointed straight at Netlify with DNS), not a domain that is
forwarded "with masking": masking puts the site inside a frame, and the data files cannot be read through it.
Then on a Mac: cd ios-app && npx cap sync ios && npx cap open ios
"""
from __future__ import annotations

import argparse
import json
import re
import shutil

from backend.config import ROOT
from scripts.build_deploy import CONFIG, HEAD

SITE, APP = ROOT / "site", ROOT / "ios-app"
WWW, HOSTFILE = APP / "www", APP / "data-host.txt"
# Leaflet and Chart.js go inside the app (same versions the website loads from its CDN), so the app starts
# without depending on that CDN. They come from ios-app/node_modules (npm install in ios-app).
LIBS = {"https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js": "leaflet/dist/leaflet.js",
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js": "chart.js/dist/chart.umd.js"}
FILES = ["logo-mark.png", "coach-kit.jpg", "favicon.ico", "favicon-gustiq-32.png", "icon-gustiq-192.png", "apple-touch-icon-gustiq.png"]

# Runs before the page's own script: relative data requests ("live.json", "global/meta.json", the AI coach at
# "/.netlify/functions/ask") go to the website instead of the files inside the app.
BOOT = """<script>
window.NOCKD_APP = true; window.NOCKD_DATA = %s;
(function () {
  var f = window.fetch.bind(window), ext = /^([a-z][a-z0-9+.-]*:|\\/\\/)/i;
  window.fetch = function (u, o) {
    if (typeof u === "string" && !ext.test(u)) u = new URL(u.replace(/^\\/+/, ""), window.NOCKD_DATA).href;
    return f(u, o);
  };
})();
// no signal (common out on the water): say so instead of leaving empty maps
addEventListener("DOMContentLoaded", function () {
  var b = document.createElement("div");
  b.textContent = "No internet connection. Gustiq needs a connection to load forecasts, tides and maps.";
  b.style.cssText = "position:fixed;left:0;right:0;top:0;z-index:99999;padding:calc(env(safe-area-inset-top,0px) + 8px) 14px 8px;background:#b3261e;color:#fff;font:600 14px/1.3 system-ui,sans-serif;text-align:center";
  var show = function () { b.hidden = navigator.onLine; }; show(); document.body.appendChild(b);
  addEventListener("online", function () { show(); }); addEventListener("offline", show);
});
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="website address the app loads its data from, e.g. https://example.netlify.app/")
    a = ap.parse_args()
    host = a.data or (HOSTFILE.read_text().strip() if HOSTFILE.exists() else "")
    if not re.match(r"^https?://[^/\s]+", host or ""):
        raise SystemExit("Give the website address once with --data https://your-site.netlify.app/")
    host = host.rstrip("/") + "/"
    if a.data:
        HOSTFILE.write_text(host + "\n")
    if not host.startswith("https://"):
        print("note: iPhones only load https addresses; http is for testing on a computer")
    if WWW.exists():
        shutil.rmtree(WWW)
    WWW.mkdir(parents=True)
    page = (SITE / "index.html").read_text().replace("<title>Gustiq</title>", "<title>Gustiq</title>", 1)
    (WWW / "lib").mkdir()
    for url, rel in LIBS.items():
        if url not in page:
            raise SystemExit(f"the page no longer loads {url}: update LIBS in scripts/build_app.py")
        name = rel.split("/")[-1]
        shutil.copy(APP / "node_modules" / rel, WWW / "lib" / name)
        page = page.replace(url, "lib/" + name)
    # links that open a new window go to Safari, which cannot open files inside the app: use the website's copy
    if 'href="privacy.html"' not in page:
        raise SystemExit("privacy link not found in the page: update scripts/build_app.py")
    page = page.replace('href="privacy.html"', f'href="{host}privacy.html"')
    head = HEAD.replace("<head>", "<head>" + BOOT % json.dumps(host), 1)
    (WWW / "index.html").write_text(head + page + "\n</body></html>\n")
    (WWW / "config.js").write_text(CONFIG)
    for f in FILES:
        shutil.copy(SITE / f, WWW / f)
    size = sum(p.stat().st_size for p in WWW.rglob("*") if p.is_file())
    print(f"wrote {WWW} ({size // 1024} KB), data from {host}")


if __name__ == "__main__":
    main()
