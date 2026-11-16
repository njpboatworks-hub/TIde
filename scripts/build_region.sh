#!/usr/bin/env bash
# Rebuild all coast data for the region in scripts/region.py (run from the project root).
set -e
python3 -m scripts.coast_climatology
python3 -m scripts.coast_asos
python3 -m scripts.tides_snapshot
python3 -m scripts.coast_direction_effects
python3 -m scripts.live_snapshot
python3 -m scripts.export_static --coast
echo "region build complete"
