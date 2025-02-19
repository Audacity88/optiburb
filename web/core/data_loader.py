"""
Data Loader Module

This module handles loading data from various sources including OpenStreetMap
and shapefiles for the OptiburB system.
"""

import os
import re
import osmnx
import geopandas
import shapely.geometry
from web.utils.logging import logger

class DataLoader:
    def __init__(self):
        """Initialize the DataLoader with default settings."""
        # Default OSM filter settings
        self.custom_filter = (
            '["highway"]'
            '["area"!~"yes"]'
            '["highway"!~"motorway|motorway_link|bridleway|footway|service|pedestrian|'
            'steps|stairs|escalator|elevator|construction|proposed|demolished|escape|bus_guideway|'
            'sidewalk|crossing|bus_stop|traffic_signals|stop|give_way|milestone|platform|speed_camera|'
            'raceway|rest_area|traffic_island|services|yes|no|drain|street_lamp|razed|corridor|abandoned"]'
            '["access"!~"private|no|customers"]'
            '["bicycle"!~"dismount|use_sidepath|private|no"]'
            '["service"!~"private|parking_aisle"]'
            '["motorroad"!="yes"]'
            '["golf_cart"!~"yes|designated|private"]'
            '[!"waterway"]'
            '[!"razed"]'
        )

        # OSM settings
        self.useful_tags_way = [
            'bridge', 'tunnel', 'oneway', 'lanes', 'ref', 'name', 'highway', 'maxspeed', 'service',
            'access', 'area', 'landuse', 'width', 'est_width', 'junction', 'surface',
        ]

        # Configure OSMnx settings
        osmnx.settings.useful_tags_way = self.useful_tags_way
        osmnx.settings.use_cache = True
        osmnx.settings.log_console = True

    def load_osm_data(self, location, select=1, buffer_dist=20):
        """
        Load OpenStreetMap data for a given location.
        
        Args:
            location (str): Location name or description
            select (int): Which result to use from geocoding (default: 1)
            buffer_dist (int): Buffer distance in meters around the point (default: 20)
            
        Returns:
            shapely.geometry.Polygon: The polygon representing the area
        """
        logger.info('searching for query=%s, which_result=%s', location, select)

        # First get the coordinates
        location_coords = osmnx.geocoder.geocode(location)
        if not location_coords:
            raise ValueError(f"Could not find location: {location}")
            
        # Create a point and buffer it - using a larger buffer distance
        point = shapely.geometry.Point(location_coords[1], location_coords[0])  # lon, lat
        polygon = point.buffer(500 / 111000)  # Convert meters to degrees (roughly)

        return polygon

    def load_shapefile(self, filename):
        """
        Load a shapefile into a GeoDataFrame.
        
        Args:
            filename (str): Path to the shapefile
            
        Returns:
            geopandas.GeoDataFrame: The loaded shapefile data
        """
        df = geopandas.read_file(filename)
        logger.info('df=%s', df)
        logger.info('df.crs=%s', df.crs)

        return df

    def get_shapefile_polygon(self, shapefile, key, name):
        """
        Extract a polygon from a shapefile based on a key-value match.
        
        Args:
            shapefile (geopandas.GeoDataFrame): The loaded shapefile
            key (str): The column name to match against
            name (str): The value to match in the key column
            
        Returns:
            shapely.geometry.Polygon: The extracted polygon
        """
        logger.info('shapefile=%s, key=%s, name=%s', shapefile, key, name)

        suburb = shapefile[shapefile[key] == name]
        suburb = suburb.to_crs(epsg=4326)
        logger.info('suburb=%s', suburb)

        polygon = suburb['geometry'].values[0]

        return polygon

    def get_nearest_node(self, g, point, return_dist=True):
        """
        Find the nearest node in the graph to a given point.
        
        Args:
            g (networkx.Graph): The graph to search in
            point (tuple): The point coordinates (lat, lon)
            return_dist (bool): Whether to return the distance to the node
            
        Returns:
            tuple: The nearest node ID and optionally the distance
        """
        return osmnx.distance.get_nearest_node(g, point, return_dist=return_dist)

    def process_name(self, name):
        """
        Process a location name into a filename-safe format.
        
        Args:
            name (str): The name to process
            
        Returns:
            str: The processed name
        """
        processed_name = name.lower()
        processed_name = re.sub(r'[\s,_]+', '_', processed_name)
        return processed_name 