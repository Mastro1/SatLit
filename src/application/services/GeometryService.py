import ee
import geopandas as gpd
import json
import os
from pathlib import Path
import pygadm
from shapely import force_2d

class GeometryService:
    def __init__(self):
        pass

    def parse_file(self, file_path: str) -> dict:
        """
        Quick-scan a geometry file and return lightweight metadata (no GeoDataFrame).
        
        Returns:
            dict: {'type': 'points'|'shapes', 'n_features': int, 'geom_types': list[str]}
        """
        try:
            gdf = gpd.read_file(file_path)
            
            if gdf.empty:
                raise ValueError("The file contains no geometries.")
            
            # Detect geometry types
            geom_types = list(gdf.geom_type.unique())
            point_types = {'Point', 'MultiPoint'}
            detected_type = 'points' if set(geom_types).issubset(point_types) else 'shapes'
            
            # Collect non-geometry column names for Feature ID selection
            columns = [c for c in gdf.columns if c != 'geometry']
            
            return {
                'type': detected_type,
                'n_features': len(gdf),
                'geom_types': geom_types,
                'columns': columns
            }
            
        except Exception as e:
            raise ValueError(f"Error reading geometry file: {e}")

    def load_file(self, file_path: str, simplify_tolerance: float = 0.0) -> gpd.GeoDataFrame:
        """
        Read a geometry file on-demand and return a processed GeoDataFrame.
        Called only when data is actually needed (map preview or extraction).
        
        Args:
            file_path: Path to the file
            simplify_tolerance: If > 0, simplify shape geometries (degrees). 
                                Use ~0.01 for GEE export to avoid payload limits.
        """
        gdf = gpd.read_file(file_path)
        
        if gdf.empty:
            raise ValueError("The file contains no geometries.")
        
        # Reproject to WGS84 if needed
        if gdf.crs and gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")
        elif gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        
        # Drop Z coordinates (common in KML files)
        gdf['geometry'] = gdf['geometry'].apply(force_2d)
        
        return self.simplify_shapes(gdf, simplify_tolerance)

    @staticmethod
    def simplify_shapes(gdf: gpd.GeoDataFrame, tolerance: float) -> gpd.GeoDataFrame:
        """Return a copy with shape geometries simplified (degrees).
        No-op (same object) for tolerance <= 0 or point-only data."""
        if tolerance <= 0 or set(gdf.geom_type.unique()).issubset({'Point', 'MultiPoint'}):
            return gdf
        gdf = gdf.copy()
        gdf['geometry'] = gdf.geometry.simplify(tolerance=tolerance)
        return gdf

    def parse_geometry(self, data, geometry_type: str) -> ee.Geometry:
        """
        Parses input data into an ee.Geometry object.
        
        Args:
            data: The input data (e.g., coordinate string, file path, or GADM selection).
            geometry_type: One of 'point', 'shapefile', 'gadm'.
            
        Returns:
            ee.Geometry: The parsed Earth Engine geometry.
        """
        if geometry_type == 'point':
            return self._parse_point(data)
        elif geometry_type == 'shapefile':
            return self._parse_shapefile(data)
        elif geometry_type == 'gadm':
            return self._parse_gadm(data)
        else:
            raise ValueError(f"Unknown geometry type: {geometry_type}")

    def _parse_point(self, data: dict) -> ee.Geometry:
        """Parses lat/lon dictionary."""
        try:
            lat = float(data.get('lat'))
            lon = float(data.get('lon'))
            return ee.Geometry.Point([lon, lat])
        except (ValueError, TypeError):
            raise ValueError("Invalid coordinates provided.")

    def _parse_shapefile(self, file_path: str) -> ee.Geometry:
        """
        Parses a shapefile or GeoJSON using geopandas and converts to ee.Geometry.
        """
        try:
            gdf = gpd.read_file(file_path)
            
            # Reproject to WGS84 if needed
            if gdf.crs != "EPSG:4326":
                gdf = gdf.to_crs("EPSG:4326")
            
            # Convert to GeoJSON
            geojson_str = gdf.to_json()
            geojson_dict = json.loads(geojson_str)
            
            # Extract features or geometry
            # For simplicity, we create a specialized collection or geometry wrapper
            # ee.Geometry(geojson_geometry)
            
            # Initial handling: define a MultiPolygon or FeatureCollection depending on use case.
            # Best practice: Uploading complex geometries often requires managing feature collections.
            # For extraction, usually we want the geometry of the ROI.
            
            # Combining all geometries in the file into one MultiPolygon/Polygon
            combined_geom = gdf.geometry.unary_union
            
            # Convert shapely geometry to GeoJSON dict
            if combined_geom.geom_type == 'Polygon':
                coords = [list(x) for x in combined_geom.exterior.coords]
                return ee.Geometry.Polygon(coords)
            elif combined_geom.geom_type == 'MultiPolygon':
               # Handle multipolygon conversion manually or via geojson interface
               # ee.Geometry.MultiPolygon takes a list of lists of lists of coordinates
               pass

            # Fallback: simple feature collection from geojson
            fc = ee.FeatureCollection(geojson_dict)
            return fc.geometry()

        except Exception as e:
            raise ValueError(f"Error processing shapefile: {e}")

    def _parse_gadm(self, data: dict) -> ee.Geometry:
        """
        Retrieves geometry using pygadm.
        data expected keys: 'country', 'admin_level', 'region'
        """
        try:
            # Example usage of pygadm (pseudocode as specific API might vary)
            # pygadm.get_items(name=country, content_level=1)
            # This requires knowing the exact library usage. 
            # Assuming data contains 'name' e.g., 'Italy', and 'admin_level' e.g., 1
            
            name = data.get('name')
            admin_level = data.get('admin_level', 0)
            
            gdf = pygadm.get_items(name=name, content_level=admin_level)
            
            if gdf.empty:
                raise ValueError(f"No GADM data found for {name} at level {admin_level}")
                
            # Convert to ee.Geometry similar to shapefile
            geojson_str = gdf.to_json()
            geojson_dict = json.loads(geojson_str)
            fc = ee.FeatureCollection(geojson_dict)
            return fc.geometry()
            
        except Exception as e:
            raise ValueError(f"Error retrieving GADM data: {e}")
