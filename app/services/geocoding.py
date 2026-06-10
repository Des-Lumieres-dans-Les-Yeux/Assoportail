import logging

import httpx

logger = logging.getLogger(__name__)


def geocode_address(address: str, city: str, zip_code: str) -> tuple[float | None, float | None]:
    """
    Convert an address to (latitude, longitude) using OpenStreetMap Nominatim.
    Returns (None, None) if not found or on error.
    """
    full_address = f"{address}, {zip_code} {city}, France"
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": full_address, "format": "json", "limit": 1}
    headers = {"User-Agent": "Assoportail/1.0 (contact@votre-association.com)"}

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])

        # Tentative plus large si l'adresse précise ne répond pas (juste ville + code postal)
        params["q"] = f"{zip_code} {city}, France"
        response = httpx.get(url, params=params, headers=headers, timeout=10.0)
        data = response.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])

    except Exception as e:
        logger.error(f"Error geocoding address {full_address}: {e}")

    return None, None
