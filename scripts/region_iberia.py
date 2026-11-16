"""Mainland coasts of Spain and Portugal (no Balearics, Canaries, Azores or Madeira)."""
BB = (-10.0, 35.95, 3.4, 43.9)  # lon_min, lat_min, lon_max, lat_max
TZ_SPAIN, TZ_PORTUGAL = "Europe/Madrid", "Europe/Lisbon"


def in_region(lat: float, lon: float) -> bool:
    if not (BB[1] <= lat <= BB[3] and BB[0] <= lon <= BB[2]):
        return False
    if lat > 42.45 and lon > 2.9:            # France (Mediterranean side)
        return False
    if lat > 43.2 and lon > -1.78:           # France (Basque coast)
        return False
    if lon > 1.0 and lat < 40.3:             # Balearic Islands
        return False
    if lat < 36.0 and lon > -5.25:           # Morocco / Ceuta / Alboran
        return False
    return True


def tz_for(lat: float, lon: float) -> str:
    """Portugal mainland uses Lisbon time; everything else here uses Madrid time."""
    if lon < -6.18 and 36.9 < lat < 42.2:
        # Galicia (Spain) is north of the Minho (~41.9 N); the Algarve/Alentejo coast is west of ~-7.4
        if lat > 41.88:
            return TZ_SPAIN
        if lat < 37.6 and lon > -7.42:
            return TZ_SPAIN
        return TZ_PORTUGAL
    return TZ_SPAIN
