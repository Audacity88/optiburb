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
from web.core import (
    GraphManager,
    GraphBalancer,
    GeometryManager,
    RouteGenerator,
    DataLoader
)
from web.utils.logging import logger

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
        """Set the starting location for the route."""
        self.start = self.data_loader.get_nearest_node(self.graph_manager.g, addr)
        logger.info('setting start point to %s', self.start)

    def load(self, options):
        """Load and prepare the graph data."""
        # Pass the custom filter from data loader to options
        options.custom_filter = self.data_loader.custom_filter
        
        # Load the graph using the graph manager
        self.graph_manager.load_graph(self.region, options)

        if options.prune:
            self.graph_manager.prune_graph()

    def determine_nodes(self):
        """Balance the graph to ensure it can support an Eulerian circuit."""
        # Balance the graph using the graph balancer
        self.graph_manager.g_working = self.balancer.balance_graph(
            self.graph_manager.g_working,
            self.graph_manager.node_coords
        )
        
        # Update the augmented graph
        self.graph_manager.g_augmented = self.graph_manager.g_working.copy()

    def optimize_dead_ends(self):
        """Optimize dead-end roads in the graph."""
        # Optimize dead ends using the graph balancer
        self.graph_manager.g_working = self.balancer.optimize_dead_ends(
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
            self.start
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
    parser.add_argument('names', type=str, nargs=argparse.REMAINDER, help='suburb names with state, country, etc')
    parser.add_argument('--debug', type=str, default='info', help='debug level debug, info, warn, etc')
    parser.add_argument('--start', type=str, help='optional starting address')
    parser.add_argument('--prune', default=False, action='store_true', help='prune unnamed gravel tracks')
    parser.add_argument('--simplify', default=False, action='store_true', help='simplify OSM nodes on load')
    parser.add_argument('--simplify-gpx', dest='simplify_gpx', default=True, action='store_true', help='reduce GPX points')
    parser.add_argument('--complex-gpx', dest='simplify_gpx', action='store_false', help='leave all the OSM points in the GPX output')
    parser.add_argument('--select', type=int, default=1, help='select the nth item from the search results')
    parser.add_argument('--shapefile', type=str, default=None, help='filename of shapefile to load localities, comma separated by the column to match on')
    parser.add_argument('--buffer', type=int, dest='buffer', default=20, help='buffer distance around polygon')
    parser.add_argument('--save-fig', default=False, action='store_true', help='save an SVG image of the nodes and edges')
    parser.add_argument('--feature-deadend', default=False, action='store_true', help='experimental feature to optimize deadends in solution')

    args = parser.parse_args()

    logger.setLevel(logging.getLevelName(args.debug.upper()))
    logger.debug('called with args - %s', args)

    start_time = datetime.datetime.now()

    burbing = Burbing()

    if not args.names:
        parser.print_help()
        return 1

    if args.shapefile:
        filename, key = args.shapefile.split(',')
        shapefile = burbing.data_loader.load_shapefile(filename)
        for name in args.names:
            polygon = burbing.data_loader.get_shapefile_polygon(shapefile, key, name)
            burbing.add_polygon(polygon, name)
    else:
        for name in args.names:
            polygon = burbing.data_loader.load_osm_data(name, args.select, args.buffer)
            burbing.add_polygon(polygon, name)

    if args.start:
        burbing.set_start_location(args.start)

    burbing.load(args)

    if args.save_fig:
        burbing.save_visualization()

    burbing.determine_nodes()

    if args.feature_deadend:
        burbing.optimize_dead_ends()

    burbing.determine_circuit()

    burbing.create_gpx_track(args.simplify_gpx)

    end_time = datetime.datetime.now()
    logger.info('elapsed time = %s', end_time - start_time)

    return 0

if __name__ == '__main__':
    exit(main())

