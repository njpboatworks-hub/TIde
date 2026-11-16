# SailIQ Wind (prototype)

Venue wind intelligence for **pre-race planning** and **coach debriefs**, starting with Newport, RI.
The goal is to give visiting sailors the kind of local wind knowledge that usually takes years on the water to build.

## Run it

You need Python 3.10 or newer (check with `python3 --version`).

**Mac / Linux:**
```bash
./start.sh
```
Then open http://localhost:8000.

The first run takes a minute or two: it installs libraries, downloads about 20 MB of NOAA history and trains the model. After that it starts in seconds.

## What it does

| Tab | What you get | Where it comes from |
|---|---|---|
| **Race Planner** | Enter the forecast at race start. You see how the wind actually moved on every similar past day (left, right or oscillating), the typical oscillation size and cycle, and whether the breeze builds or fades. | 6 years (2019 to 2024) of NOAA station data at 4 stations, matched on month, start time, direction and speed |
| **Live Today** | Current readings around the bay and an AI forecast of the shift at Newport harbor over the next 2 hours, with an 80% range. | NOAA real-time feeds and a gradient-boosted model |
| **Coach Debrief** | Upload any GPX track. The app infers the wind from the boat's tacking angles, colors the track by lifted or headed, and checks whether each tack was made on a header. | The boat's own GPS track |
| **Venue lift map** | A map of where the wind tends to be shifted left or right on the course. It improves as more tracks are uploaded. | Saved debrief tracks, comparing boats sailing in the same race |
| **Model Accuracy** | Results from testing the model on a full season it never saw in training. | 2024 held out as the test year |

### Measured results (not projections)
- **Shift forecast (2024 test season):** average error 13.9° vs 15.1° for assuming no shift. That is a real but modest gain. On shifts of 10° or more it called the side correctly 71% of the time, against 48% for climatology.
- **Debrief wind inference (synthetic test tracks with known true wind):** inferred shifts correlated 0.89 to 0.96 with the truth, with about 1 to 2° of typical error.
- **Lift map (40 synthetic tracks with a known built-in gradient):** the map correlated about 0.8 with the true pattern but understated its size by roughly 30 to 50%.

## Important limitations
- **Station data is not race-course data.** The planner's statistics come from the Newport harbor station (10-minute averages). Shifts on the race course will be faster and larger.
- **GPS gives course over ground, not heading.** Tidal current and leeway end up in the inferred wind. Newport has significant current.
- **The demo tracks in `samples/` are SYNTHETIC.** They use real station wind as a base plus simulated oscillations and a simulated geographic shift. Replace them with real fleet tracks before drawing any conclusions about Newport.
- **The lift map needs real data volume.** Several boats from the same race are worth far more than many solo sessions.
- **Racing rules:** RRS rule 41 (Outside Help), class rules and sailing instructions may restrict electronic information during a race. This prototype is designed for before and after racing.

## Project layout
```
backend/
  config.py     venues and stations (add a new venue here)
  data.py       NOAA download, parsing and 10-minute resampling
  analysis.py   analog-day planner and hourly climatology
  model.py      shift-forecast model, training and backtest
  debrief.py    GPX parsing, wind inference from tacks, lift map
  app.py        FastAPI web server (API + serves the frontend)
frontend/       index.html, app.js, style.css (no build step)
scripts/make_sample_gpx.py   synthetic demo track generator
samples/        SYNTHETIC demo GPX files
data/           downloaded data, cache, model and saved tracks (created on first run)
```

## Useful commands
```bash
./.venv/bin/python -m backend.data newport --refresh   # re-download NOAA data
./.venv/bin/python -m backend.model                    # retrain and print the backtest
```

## Adding another ocean venue
Add an entry to `VENUES` in `backend/config.py` with nearby NOAA NDBC station IDs (look them up on the NDBC website). Pick one station as `primary` (closest to the race area) and one as `gradient` (open water). Then run the two commands above.

Data: NOAA National Data Buoy Center. Map tiles: OpenStreetMap (for production, use a tile provider whose terms fit commercial use).

## East Coast data (hosted page)
Refresh the live readings and 7-day NWS forecast (about 20 seconds once the NWS grid lookups are cached):
```bash
python -m scripts.live_snapshot
```
Refresh tide and current predictions (predictions do not go stale, but the window is today to +8 days):
```bash
python -m scripts.tides_snapshot
```
Rebuild station history (NOAA buoys, coastal airports) and how each spot bends the breeze:
```bash
python -m scripts.coast_climatology && python -m scripts.coast_asos && python -m scripts.coast_direction_effects && python -m scripts.export_static --coast
```
Sources: NOAA NDBC, NOAA CO-OPS, National Weather Service (api.weather.gov), Iowa Environmental Mesonet (ASOS), GSHHG shoreline (LGPL).
