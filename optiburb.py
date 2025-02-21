#!/usr/bin/env python3.9

"""
OptiburB - Optimized Urban Route Generator

This tool generates optimized routes for exploring urban areas, ensuring complete
coverage of all streets while minimizing backtracking. It uses OpenStreetMap data
and supports both shapefile and direct location inputs.
"""

import argparse
import datetime
import logging
import os
import shapely.geometry
import networkx as nx
from web.core import (
    GraphManager,
    GraphBalancer,
    GeometryManager,
    RouteGenerator,
    DataLoader
)
from web.utils.logging import logger
import osmnx
import gpxpy

class Burbing:
    WARNING = '''Note: This program now considers one-way streets and road directionality. Please still verify routes for safety.'''

    def __init__(self):
        """Initialize the Burbing system with its core components."""
        # Initialize core components
        self.geometry = GeometryManager()
        self.data_loader = DataLoader()
        self.graph_manager = GraphManager()
        self.balancer = GraphBalancer(self.geometry)
        self.route_generator = RouteGenerator(self.geometry)

        # State variables
        self.region = shapely.geometry.Polygon()
        self.name = ''
        self.start = None
        self.start_addr = None  # Store the start address
        self.polygons = {}

        logger.warning(self.WARNING)

    def add_polygon(self, polygon, name):
        """Add a polygon to the region with a given name."""
        self.polygons[name] = polygon
        self.region = self.region.union(polygon)

        if self.name:
            self.name += '_'

        processed_name = self.data_loader.process_name(name)
        self.name += processed_name

    def set_start_location(self, addr):
        """Store the starting location address for later use."""
        self.start_addr = addr
        logger.info('Stored start address: %s', addr)

    def _set_start_node(self):
        """Set the start node after the graph is loaded."""
        if not self.start_addr:
            return
        
        try:
            # First geocode the address to get coordinates
            coords = osmnx.geocode(self.start_addr)
            if coords is None:
                raise ValueError(f"Could not find location: {self.start_addr}")
            
            # Find nearest node using the coordinates, don't return distance
            self.start = self.data_loader.get_nearest_node(self.graph_manager.g, coords, return_dist=False)
            logger.info('Set start point to %s (address: %s)', self.start, self.start_addr)
        except Exception as e:
            logger.error(f"Error setting start location: {str(e)}")
            raise ValueError(f"Could not set start location: {str(e)}")

    def load(self, options):
        """Load and prepare the graph data."""
        # Pass the custom filter from data loader to options
        options.custom_filter = self.data_loader.custom_filter
        
        # Load the graph using the graph manager
        self.graph_manager.load_graph(self.region, options)

        if options.prune:
            self.graph_manager.prune_graph()
            
        # Set the start node after the graph is loaded
        if self.start_addr:
            self._set_start_node()

    def determine_nodes(self):
        """Balance the graph to ensure it can support an Eulerian circuit."""
        # Balance the graph using the graph balancer
        self.graph_manager.g_working = self.balancer.balance_graph(
            self.graph_manager.g_working,
            self.graph_manager.node_coords
        )
        
        # Update the augmented graph
        self.graph_manager.g_augmented = self.graph_manager.g_working.copy()

    def determine_circuit(self):
        """Find an Eulerian circuit in the graph."""
        # Use the route generator to find the circuit
        self.euler_circuit = self.route_generator.determine_circuit(
            self.graph_manager.g_augmented,
            self.start,
            completed_area=self.completed_area if hasattr(self, 'completed_area') else None
        )

    def create_gpx_track(self, simplify=False):
        """Create a GPX track from the Eulerian circuit."""
        # Use the route generator to create the GPX track
        return self.route_generator.create_gpx_track(
            self.graph_manager.g_augmented,
            self.euler_circuit,
            simplify
        )

    def save_visualization(self):
        """Save a visualization of the graph."""
        filename = f'burb_nodes_{self.name}.svg'
        self.graph_manager.save_visualization(filename)

def main():
    parser = argparse.ArgumentParser(description='Optimum Suburb Route Generator')
    parser.add_argument('location', type=str, help='location name (e.g. "West Hartford, CT")')
    parser.add_argument('--debug', type=str, default='info', help='debug level debug, info, warn, etc')
    parser.add_argument('--log-file', type=str, help='file to write logs to')
    parser.add_argument('--start', type=str, help='optional starting address')
    parser.add_argument('--prune', default=False, action='store_true', help='prune unnamed gravel tracks')
    parser.add_argument('--simplify', default=False, action='store_true', help='simplify OSM nodes on load')
    parser.add_argument('--simplify-gpx', dest='simplify_gpx', default=True, action='store_true', help='reduce GPX points')
    parser.add_argument('--complex-gpx', dest='simplify_gpx', action='store_false', help='leave all the OSM points in the GPX output')
    parser.add_argument('--select', type=int, default=1, help='select the nth item from the search results')
    parser.add_argument('--shapefile', type=str, default=None, help='filename of shapefile to load localities, comma separated by the column to match on')
    parser.add_argument('--buffer', type=int, dest='buffer', default=500, help='buffer distance in meters around polygon')
    parser.add_argument('--save-fig', default=False, action='store_true', help='save an SVG image of the nodes and edges')
    parser.add_argument('--completed-roads', type=str, help='GPX file containing completed roads to exclude')

    args = parser.parse_args()

    # Configure logging
    log_level = logging.getLevelName(args.debug.upper())
    logger.setLevel(log_level)
    
    # Add file handler if log file specified
    if args.log_file:
        file_handler = logging.FileHandler(args.log_file)
        file_handler.setLevel(log_level)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info('Logging to file: %s', args.log_file)

    logger.debug('called with args - %s', args)

    start_time = datetime.datetime.now()

    burbing = Burbing()

    # Convert buffer from meters to degrees (approximately)
    buffer_degrees = args.buffer / 111000  # 1 degree ≈ 111km
    logger.info(f"Using buffer size: {args.buffer}m ({buffer_degrees:.6f} degrees)")

    if args.shapefile:
        filename, key = args.shapefile.split(',')
        shapefile = burbing.data_loader.load_shapefile(filename)
        polygon = burbing.data_loader.get_shapefile_polygon(shapefile, key, args.location)
        burbing.add_polygon(polygon, args.location)
    else:
        polygon = burbing.data_loader.load_osm_data(args.location, args.select, buffer_degrees)
        burbing.add_polygon(polygon, args.location)

    if args.start:
        burbing.set_start_location(args.start)

    # Load completed roads if specified
    if args.completed_roads:
        try:
            with open(args.completed_roads, 'r') as f:
                gpx = gpxpy.parse(f)
            
            # Create a buffer around the GPX track
            points = []
            for track in gpx.tracks:
                for segment in track.segments:
                    for point in segment.points:
                        points.append((point.longitude, point.latitude))
            
            if points:
                # Create a LineString from the points and buffer it
                line = shapely.geometry.LineString(points)
                buffer_size = 0.00005  # About 5 meters
                burbing.completed_area = line.buffer(buffer_size)
                if not burbing.completed_area.is_valid:
                    burbing.completed_area = burbing.completed_area.buffer(0)
                logger.info(f"Loaded completed roads from {args.completed_roads}")
            else:
                logger.warning("No points found in completed roads GPX file")
        except Exception as e:
            logger.error(f"Error loading completed roads: {str(e)}")

    burbing.load(args)

    if args.save_fig:
        burbing.save_visualization()

    burbing.determine_nodes()

    burbing.determine_circuit()

    burbing.create_gpx_track(args.simplify_gpx)

    end_time = datetime.datetime.now()
    logger.info('elapsed time = %s', end_time - start_time)

    return 0

if __name__ == '__main__':
    exit(main())

