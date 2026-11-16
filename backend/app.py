"""SailIQ Wind API server.

Run:  uvicorn backend.app:app --port 8000
Then open http://localhost:8000
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, debrief, model
from .config import ROOT, VENUES
from .data import fetch_realtime, resample_10min

app = FastAPI(title="SailIQ Wind", version="0.1.0")


def _venue(v: str) -> dict:
    if v not in VENUES:
        raise HTTPException(404, f"Unknown venue '{v}'")
    return VENUES[v]


@app.get("/api/venues")
def venues():
    return {k: {"name": c["name"], "center": c["center"], "zoom": c["zoom"], "stations": c["stations"],
                "season_months": c["season_months"]} for k, c in VENUES.items()}


class PlanRequest(BaseModel):
    months: list[int] = Field(..., min_length=1)
    start_hour: float = Field(..., ge=0, lt=24)
    duration_h: float = Field(2.5, gt=0, le=8)
    direction: float = Field(..., ge=0, lt=360)
    speed: float = Field(..., ge=0, le=60)
    dir_tol: float = Field(25, gt=0, le=180)
    spd_tol: float = Field(4, gt=0, le=30)


@app.post("/api/venue/{venue}/plan")
def plan(venue: str, req: PlanRequest):
    _venue(venue)
    return analysis.plan(venue, **req.model_dump())


@app.get("/api/venue/{venue}/climatology")
def climatology(venue: str, month: int):
    _venue(venue)
    if not 1 <= month <= 12:
        raise HTTPException(400, "month must be 1-12")
    return analysis.climatology(venue, month)


@app.get("/api/venue/{venue}/model")
def model_metrics(venue: str):
    _venue(venue)
    return model.load_metrics(venue)


@app.get("/api/venue/{venue}/live")
def live(venue: str):
    cfg = _venue(venue)
    frames, latest = {}, {}
    for sid, meta in cfg["stations"].items():
        rt = fetch_realtime(sid)
        if rt is None or rt.empty:
            continue
        r10 = resample_10min(rt)
        frames[sid] = r10
        last = rt.dropna(subset=["WDIR"]).tail(1)
        if len(last):
            latest[sid] = {**meta, "time_local": last.index[0].tz_convert(cfg["tz"]).strftime("%Y-%m-%d %H:%M"),
                           "dir": round(float(last["WDIR"].iloc[0])), "speed": round(float(last["WSPD_KT"].iloc[0]), 1),
                           "gust": None if pd.isna(last["GST_KT"].iloc[0]) else round(float(last["GST_KT"].iloc[0]), 1)}
    if cfg["primary"] not in frames:
        raise HTTPException(503, "Live data for the primary station is unavailable right now.")
    # Put all stations on one shared grid ending at the primary station's latest time.
    idx = frames[cfg["primary"]].index
    cols = frames[cfg["primary"]].columns
    frames = {sid: frames[sid].reindex(idx) if sid in frames
              else pd.DataFrame(float("nan"), index=idx, columns=cols, dtype=float)
              for sid in cfg["stations"]}
    pred = model.predict_from_frames(venue, frames)
    now_local = pd.Timestamp.now(tz=cfg["tz"])
    warnings = []
    if now_local.month not in cfg["season_months"] or not (cfg["day_start_hour"] <= now_local.hour <= cfg["day_end_hour"] - 2):
        warnings.append("The model was trained on daytime racing-season data (May to October). "
                        "Predictions outside those months and hours are less reliable.")
    if pred and pred["current_speed"] < model.MIN_SPEED_KT:
        warnings.append(f"Current wind is below {model.MIN_SPEED_KT} kt. Direction readings are unreliable in light air, "
                        "so treat this forecast as noise.")
    missing = [cfg["stations"][sid]["name"] for sid in cfg["stations"] if sid not in latest]
    if missing:
        warnings.append("No live wind right now from: " + ", ".join(missing) + ". The forecast uses the stations that are reporting.")
    return {"stations": latest, "prediction": pred, "warnings": warnings,
            "metrics": model.load_metrics(venue)}


@app.post("/api/debrief")
async def upload_debrief(file: UploadFile = File(...), name: str = Form(""), venue: str = Form("newport"),
                         twd_hint: float | None = Form(None)):
    _venue(venue)
    content = await file.read()
    if len(content) > 20_000_000:
        raise HTTPException(413, "File too large (20 MB max).")
    try:
        df = debrief.parse_gpx(content)
        result = debrief.analyze(df, venue, twd_hint)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # malformed XML etc.
        raise HTTPException(400, f"Could not read GPX file: {e}")
    tid = debrief.save(result, name or file.filename or datetime.now().isoformat(), venue)
    return {"id": tid, **debrief.get_track(tid)}


@app.post("/api/debrief/load-samples")
def load_samples(venue: str = "newport"):
    """Import the SYNTHETIC demo tracks in /samples (skips ones already imported)."""
    existing = {t["name"] for t in debrief.list_tracks()}
    added = []
    for p in sorted((ROOT / "samples").glob("*.gpx")):
        name = p.stem.replace("_", " ")
        if name in existing:
            continue
        result = debrief.analyze(debrief.parse_gpx(p.read_bytes()), venue)
        added.append(debrief.save(result, name, venue))
    return {"added": len(added)}


@app.get("/api/debriefs")
def debriefs(venue: str | None = None):
    return debrief.list_tracks(venue)


@app.get("/api/debrief/{tid}")
def get_debrief(tid: str):
    try:
        return debrief.get_track(tid)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")


@app.delete("/api/debrief/{tid}")
def del_debrief(tid: str):
    debrief.delete_track(tid)
    return {"ok": True}


@app.get("/api/venue/{venue}/liftmap")
def liftmap(venue: str, direction: float | None = None, tol: float = 30):
    _venue(venue)
    return debrief.lift_map(venue, direction, tol)


FRONTEND = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
