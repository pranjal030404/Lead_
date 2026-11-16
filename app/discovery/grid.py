"""Grid sweep: beat the ~20-results-per-query ceiling of map providers.

A dense city returns the same top results for one query. Tiling the area into
overlapping circles and querying each cell surfaces the long tail. Pure geometry.
"""

from __future__ import annotations

import math

EARTH_M = 6_371_000.0


def _offset(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = math.degrees(north_m / EARTH_M)
    dlng = math.degrees(east_m / (EARTH_M * math.cos(math.radians(lat))))
    return lat + dlat, lng + dlng


def grid_cells(lat: float, lng: float, radius_m: int, cell_radius_m: int = 1500,
               max_cells: int = 49) -> list[dict]:
    """Hex-packed circle centres covering a disc of `radius_m` around (lat, lng).

    Cells are spaced sqrt(3)*r apart so neighbouring circles overlap and leave no
    gaps. Returned centre-first, then outward, so a cap drops the outskirts.
    """
    if radius_m <= 0 or cell_radius_m <= 0:
        raise ValueError("radius_m and cell_radius_m must be positive")
    cell_radius_m = min(cell_radius_m, radius_m)
    step = cell_radius_m * math.sqrt(3)
    rows = int(radius_m // (step * 0.75)) + 1
    cells = []
    for row in range(-rows, rows + 1):
        north = row * step * 0.75
        shift = (step / 2) if row % 2 else 0
        cols = int(radius_m // step) + 1
        for col in range(-cols, cols + 1):
            east = col * step + shift
            dist = math.hypot(north, east)
            if dist <= radius_m + cell_radius_m * 0.5:
                clat, clng = _offset(lat, lng, north, east)
                cells.append({"lat": round(clat, 6), "lng": round(clng, 6),
                              "radius_m": int(cell_radius_m), "distance_m": int(dist)})
    cells.sort(key=lambda c: c["distance_m"])
    return cells[:max_cells]
