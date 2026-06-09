"""
Grid Utilities for GEE Data Extractor.

Handles offline calculation of satellite pixel grids using dataset crs and transform metadata.
Supports coordinate transformation and intersection checking with shapely.
"""

import math
from shapely.geometry import Polygon, mapping
import shapely.ops
from pyproj import Transformer


def generate_pixel_grid_geojson(roi_geom, crs, transform, max_pixels=1000):
    """
    Generate a GeoJSON FeatureCollection of pixel boundaries intersecting the ROI.

    Args:
        roi_geom: shapely.geometry.base.BaseGeometry in EPSG:4326 (WGS84)
        crs: str, the CRS of the dataset (e.g. "EPSG:4326")
        transform: list of 6 floats, the affine transform matrix of the dataset:
                   [dx, 0, x_0, 0, dy, y_0] where:
                     dx = pixel width (a)
                     0 = rotation parameter (b)
                     x_0 = origin x (c)
                     0 = rotation parameter (d)
                     dy = pixel height (e)
                     y_0 = origin y (f)
        max_pixels: int, maximum number of pixel grid cells to return to prevent rendering lag.

    Returns:
        dict: GeoJSON FeatureCollection with the grid cells, or None if too many pixels.
    """
    if not crs or not transform or len(transform) < 6:
        return None

    a, b, c, d, e, f = transform
    
    # We assume b and d are 0 (no rotation), which is standard for GEE gridded datasets.
    if abs(b) > 1e-7 or abs(d) > 1e-7:
        # Fallback/warning if rotation exists, but none of the current datasets have rotation.
        pass

    # 1. Transform ROI geometry to the native CRS to calculate grid coordinates
    is_native_wgs84 = crs.upper() == "EPSG:4326"
    
    if is_native_wgs84:
        roi_native = roi_geom
        to_native = None
        to_wgs84 = None
    else:
        try:
            # Set up transformers
            to_native = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
            to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            
            # Reproject ROI geometry to native CRS
            roi_native = shapely.ops.transform(to_native.transform, roi_geom)
        except Exception as err:
            print(f"Error reprojecting ROI to native CRS ({crs}): {err}")
            return None

    # Get bounds in native CRS
    min_x, min_y, max_x, max_y = roi_native.bounds

    # 2. Determine index ranges (col, row)
    # x = a * col + c  =>  col = (x - c) / a
    # y = e * row + f  =>  row = (y - f) / e
    # Note: e (pixel height) is typically negative for north-up coordinates (latitude decreases as row index increases)
    col_coords = [(min_x - c) / a, (max_x - c) / a]
    col_min_idx = math.floor(min(col_coords))
    col_max_idx = math.floor(max(col_coords))

    row_coords = [(min_y - f) / e, (max_y - f) / e]
    row_min_idx = math.floor(min(row_coords))
    row_max_idx = math.floor(max(row_coords))

    num_cols = col_max_idx - col_min_idx + 1
    num_rows = row_max_idx - row_min_idx + 1
    total_estimated_pixels = num_cols * num_rows

    # Guard against visual lag and memory issues
    if total_estimated_pixels > max_pixels:
        return {
            "type": "FeatureCollection",
            "features": [],
            "properties": {
                "error": "too_many_pixels",
                "message": f"Grid contains {total_estimated_pixels} pixels (limit: {max_pixels}). Please zoom in or use a smaller region."
            }
        }

    features = []
    pixel_count = 0

    # 3. Iterate over the grid cells
    for col in range(col_min_idx, col_max_idx + 1):
        for row in range(row_min_idx, row_max_idx + 1):
            # Calculate pixel boundaries in native CRS
            x_left = col * a + c
            x_right = (col + 1) * a + c
            y_top = row * e + f
            y_bottom = (row + 1) * e + f
            
            # Sort coordinates to create a valid Polygon
            x_min, x_max = min(x_left, x_right), max(x_left, x_right)
            y_min, y_max = min(y_top, y_bottom), max(y_top, y_bottom)

            # Create pixel polygon in native CRS
            pixel_poly_native = Polygon([
                (x_min, y_min),
                (x_min, y_max),
                (x_max, y_max),
                (x_max, y_min),
                (x_min, y_min)
            ])

            # Check if this pixel intersects the ROI geometry
            if not roi_native.intersects(pixel_poly_native):
                continue

            # 4. Convert pixel back to WGS84 for Folium rendering
            if is_native_wgs84:
                pixel_poly_wgs84 = pixel_poly_native
            else:
                pixel_poly_wgs84 = shapely.ops.transform(to_wgs84.transform, pixel_poly_native)

            # Calculate center coordinate for labeling/reference
            center = pixel_poly_wgs84.centroid
            
            features.append({
                "type": "Feature",
                "geometry": mapping(pixel_poly_wgs84),
                "properties": {
                    "pixel_id": pixel_count + 1,
                    "col": col,
                    "row": row,
                    "center_lat": round(center.y, 6),
                    "center_lon": round(center.x, 6)
                }
            })
            pixel_count += 1

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "total_pixels": pixel_count,
            "crs": crs
        }
    }
