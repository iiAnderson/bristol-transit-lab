"""Terrain model for the clip box (plans/P3.md D1): one single-band GeoTIFF in metres,
WGS84, built from a national grid of British National Grid tiles (ASCII grid, one zip
per 10 km tile inside the national zip). Source, version and paths come from config.
"""
from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path

import numpy as np

LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"          # the National Grid has no I


def grid_square(e: float, n: float) -> str:
    """British National Grid 10 km tile name (e.g. two letters and two digits, lower
    case) for an easting / northing in metres."""
    e100, n100 = int(e // 100_000), int(n // 100_000)
    l1 = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
    l2 = (19 - n100) * 5 % 25 + e100 % 5
    return (LETTERS[l1] + LETTERS[l2]).lower() + f"{int(e % 100_000 // 10_000)}{int(n % 100_000 // 10_000)}"


def tiles_for(box_bng: tuple[float, float, float, float]) -> list[str]:
    x0, y0, x1, y1 = box_bng
    return sorted({grid_square(e, n)
                   for e in range(int(x0 // 10_000) * 10_000, int(x1) + 1, 10_000)
                   for n in range(int(y0 // 10_000) * 10_000, int(y1) + 1, 10_000)})


def build(national_zip: Path, box_wgs84: tuple[float, float, float, float], out: Path,
          res_deg: float) -> dict:
    """Mosaic the tiles covering the box and warp to WGS84 at ``res_deg`` (bilinear)."""
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.transform import from_origin
    box_bng = transform_bounds("EPSG:4326", "EPSG:27700", *box_wgs84, densify_pts=21)
    want = tiles_for(box_bng)
    x0 = int(box_bng[0] // 10_000) * 10_000
    y1 = (int(box_bng[3] // 10_000) + 1) * 10_000
    nx = (int(box_bng[2] // 10_000) + 1) * 10_000 - x0
    ny = y1 - int(box_bng[1] // 10_000) * 10_000
    cell, found = None, []
    mosaic = None
    with zipfile.ZipFile(national_zip) as nz:
        names = {Path(n).name.split("_")[0].lower(): n for n in nz.namelist()
                 if n.lower().endswith(".zip")}
        for t in want:
            if t not in names:                    # sea tiles are not published
                continue
            with zipfile.ZipFile(io.BytesIO(nz.read(names[t]))) as tz:
                asc = next(n for n in tz.namelist() if n.lower().endswith(".asc"))
                lines = tz.read(asc).decode("ascii").splitlines()
            hdr = {k.lower(): float(v) for k, v in (ln.split() for ln in lines[:5])}
            if cell is None:
                cell = hdr["cellsize"]
                mosaic = np.zeros((int(ny / cell), int(nx / cell)), dtype="float32")
            elif hdr["cellsize"] != cell:
                raise RuntimeError(f"terrain tile {t}: cell size {hdr['cellsize']} != {cell}")
            arr = np.array([ln.split() for ln in lines[5:]], dtype="float32")
            if arr.shape != (int(hdr["nrows"]), int(hdr["ncols"])):
                raise RuntimeError(f"terrain tile {t}: shape {arr.shape} does not match its header")
            c = int((hdr["xllcorner"] - x0) / cell)
            r = int((y1 - hdr["yllcorner"]) / cell) - arr.shape[0]
            mosaic[r:r + arr.shape[0], c:c + arr.shape[1]] = arr
            found.append(t)
    if not found:
        raise RuntimeError(f"no terrain tiles for {box_wgs84} in {national_zip}")
    tr = from_origin(x0, y1, cell, cell)
    w = math.ceil((box_wgs84[2] - box_wgs84[0]) / res_deg)
    h = math.ceil((box_wgs84[3] - box_wgs84[1]) / res_deg)
    dst = np.zeros((h, w), dtype="float32")
    dtr = from_origin(box_wgs84[0], box_wgs84[3], res_deg, res_deg)
    reproject(mosaic, dst, src_transform=tr, src_crs="EPSG:27700",
              dst_transform=dtr, dst_crs="EPSG:4326", resampling=Resampling.bilinear,
              dst_nodata=0.0)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs="EPSG:4326", transform=dtr,
                       compress="deflate") as f:
        f.write(dst, 1)
    return {"tiles_wanted": len(want), "tiles_found": len(found),
            "tiles_missing_sea": sorted(set(want) - set(found)), "cell_m": cell,
            "width": w, "height": h, "res_deg": res_deg,
            "min_m": float(dst.min()), "max_m": float(dst.max()), "mean_m": float(dst.mean())}


def for_function(tif: Path, cost_function: str) -> Path:
    """A copy of the raster that is specific to one slope cost function.

    r5py caches a built network under a digest of its input *files*; the cost function
    is not part of that key, so a network built with one function is silently reused for
    the other (measured in the D1 spike, plans/P3.md §9). Tagging the copy with the
    function's name changes its bytes, and so the cache key.
    """
    import rasterio
    out = tif.with_name(f"{tif.stem}.{cost_function.lower()}.tif")
    if not out.exists() or out.stat().st_mtime < tif.stat().st_mtime:
        with rasterio.open(tif) as src:
            prof, data = src.profile, src.read()
        with rasterio.open(out, "w", **prof) as dst:
            dst.write(data)
            dst.update_tags(slope_cost_function=cost_function)
    return out
