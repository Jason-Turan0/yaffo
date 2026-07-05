"""Reverse geocoding via OpenStreetMap Nominatim.

The single implementation shared by the Locations screen (routes/locations.py) and
the assign_location_name automation. Returns a human-friendly place name built from
the address components, falling back to Nominatim's display_name.
"""
from typing import Optional

import requests

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "PhotoOrganizer/1.0"
# Address keys, most-to-least specific, joined into the location name.
_ADDRESS_KEYS = ["city", "town", "village", "county", "state", "country"]


class ReverseGeocodeRateLimited(Exception):
    """The geocoding provider rejected the request due to rate limiting."""


def reverse_geocode(lat: float, lon: float, timeout: float = 3) -> Optional[str]:
    """Return a location name for the coordinate, or None if the lookup fails.

    Nominatim's usage policy caps callers at ~1 request/second; bulk callers must
    throttle themselves (the automation does).
    """
    try:
        response = requests.get(
            _NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
    except requests.RequestException:
        return None

    if response.status_code == 429:
        raise ReverseGeocodeRateLimited
    if response.status_code != 200:
        return None

    data = response.json()
    address = data.get("address", {})
    parts = [address[key] for key in _ADDRESS_KEYS if key in address]
    return ", ".join(parts) if parts else (data.get("display_name") or None)
