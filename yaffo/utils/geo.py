"""Geospatial helpers."""
from math import asin, cos, radians, sin, sqrt

_EARTH_RADIUS_M = 6_371_000


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    lat1_r, lat2_r = radians(lat1), radians(lat2)
    d_lat = lat2_r - lat1_r
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(d_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(a))
