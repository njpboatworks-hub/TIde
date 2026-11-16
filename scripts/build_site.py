"""Build site/index.html from site/template.html by inlining the Leaflet stylesheet."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tpl = (ROOT / "site" / "template.html").read_text()
leaflet_css = (ROOT / "frontend" / "vendor" / "leaflet.css").read_text()
(ROOT / "site" / "index.html").write_text(tpl.replace("/*LEAFLET_CSS*/", leaflet_css))
print("wrote site/index.html")
