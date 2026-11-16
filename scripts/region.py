"""The area SailIQ Wind covers: US East Coast and Gulf Coast (Maine to Texas).

One bounding box, minus the parts of it that are not this coastline.
"""
BB = (-97.8, 24.0, -65.5, 45.8)  # lon_min, lat_min, lon_max, lat_max


def in_region(lat: float, lon: float) -> bool:
    if not (BB[1] <= lat <= BB[3] and BB[0] <= lon <= BB[2]):
        return False
    if lon < -75.6 and lat > 40.8:          # Great Lakes, inland New York and Pennsylvania
        return False
    if lon < -81.6 and lat > 31.2:          # inland Southeast above the Gulf coast
        return False
    if lon > -79.5 and lat < 27.5:          # Bahamas
        return False
    if lon < -97.0 and lat < 25.85:         # Mexico, south of the Rio Grande
        return False
    return True
