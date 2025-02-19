#!/usr/bin/env python3.9

# this is a undirected graph version.. one-way streets and multi-edges
# are reduced, which means:

# WARNING - the resulting paths are not guaranteed to be rideable or
# safe.  You must confirm the path yourself.

import math
import time
import os
import sys
import re
import shapely
import logging
import geopandas
import osmnx
import networkx as nx
import numpy as np
import itertools
import argparse
import gpxpy
import gpxpy.gpx
import datetime
from shapely.geometry import LineString

# Configure logging to always show warnings and errors
logging.basicConfig(
    format='%(asctime)-15s %(filename)s:%(funcName)s:%(lineno)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.WARNING,
    force=True
)
log = logging.getLogger(__name__)

class Burbing:

    WARNING = '''Note: This program now considers one-way streets and road directionality. Please still verify routes for safety.'''

    def __init__(self):
        self.g = None
        self.g_working = None  # Working copy of the graph
        self.g_augmented = None  # Augmented graph for path finding
        self.is_directed = True  # Default to directed graphs

        self.polygons = {}
        self.region = shapely.geometry.Polygon()
        self.name = ''
        self.start = None

        #
        # filters to roughly match those used by rendrer.earth (see
        # https://wandrer.earth/scoring )
        #
        self.custom_filter = (

            '["highway"]'

            '["area"!~"yes"]'

            #'["highway"!~"motorway|motorway_link|trunk|trunk_link|bridleway|footway|service|pedestrian|'
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


        log.debug('custom_filter=%s', self.custom_filter)

        # not all of these fields are used at the moment, but they
        # look like fun for the future.

        useful_tags_way = [
            'bridge', 'tunnel', 'oneway', 'lanes', 'ref', 'name', 'highway', 'maxspeed', 'service',
            'access', 'area', 'landuse', 'width', 'est_width', 'junction', 'surface',
        ]

        osmnx.settings.useful_tags_way = useful_tags_way
        osmnx.settings.use_cache = True
        osmnx.settings.log_console = True

        log.warning(self.WARNING)

        return

    ##
    ##
    def add_polygon(self, polygon, name):

        self.polygons[name] = polygon

        self.region = self.region.union(polygon)

        if self.name:
            self.name += '_'
            pass

        processed_name = name.lower()
        processed_name = re.sub(r'[\s,_]+', '_', processed_name)

        self.name += processed_name

        return

    ##
    ##
    def get_osm_polygon(self, name, select=1, buffer_dist=20):

        log.info('searching for query=%s, which_result=%s', name, select)

        # First get the coordinates
        location = osmnx.geocoder.geocode(name)
        if not location:
            raise ValueError(f"Could not find location: {name}")
            
        # Create a point and buffer it - using a larger buffer distance (500 meters)
        point = shapely.geometry.Point(location[1], location[0])  # lon, lat
        polygon = point.buffer(500 / 111000)  # Convert meters to degrees (roughly)

        return polygon

    ##
    ##
    def get_shapefile_polygon(self, shapefile, key, name):

        log.info('shapefile=%s, key=%s, name=%s', shapefile, key, name)

        df = shapefile

        suburb = df[df[key] == name]
        suburb = suburb.to_crs(epsg=4326)
        log.info('suburb=%s', suburb)

        polygon = suburb['geometry'].values[0]

        return polygon

    ##
    ##
    def set_start_location(self, addr):

        point =  osmnx.geocoder.geocode(addr)
        self.start = point
        log.info('setting start point to %s', self.start)
        return

    ##
    ##
    def find_odd_nodes(self):

        # for undirected graphs

        odd_nodes = { i for i, n in self.g.degree if n % 2 == 1 }

        return odd_nodes

    ##
    ##
    def get_pair_combinations(self, nodes):
        """Get all possible directed pairs of nodes that need to be connected.
        For directed graphs, we need to consider both directions between nodes."""
        # Convert nodes to list for easier indexing
        node_list = list(nodes)
        pairs = []
        
        # For directed graphs, we need to consider both directions
        for i in range(len(node_list)):
            for j in range(len(node_list)):
                if i != j:  # Don't create self-loops
                    pairs.append((node_list[i], node_list[j]))
        
        return pairs

    ##
    ##
    def get_shortest_path_pairs(self, g, pairs):

        # XXX - consider Floyd–Warshall here instead of repeated
        # Dijkstra.  Also consider how to parallelise this as a
        # short-term speed-up, by palming off chunks to another
        # thread, except this wont work in python.

        shortest_paths = {}

        _prev_pct = 0
        _size = len(pairs)
        _prev_n = 0
        _prev_time = time.time()

        for n, pair in enumerate(pairs):
            i, j = pair
            shortest_paths[pair] = nx.dijkstra_path_length(g, i, j, weight='length')

            # Only log progress every 10%
            _cur_pct = int(100 * n / _size)
            if _cur_pct % 10 == 0 and _prev_pct != _cur_pct:
                _cur_time = time.time()
                log.info('Calculating shortest paths: %d%% complete [%d/%d]', _cur_pct, n, _size)
                _prev_time = _cur_time
                _prev_pct = _cur_pct
                _prev_n = n

        return shortest_paths

    ##
    ##
    def augment_graph(self, pairs):
        """Create new edges between node pairs using actual road paths."""
        log.info('Augmenting directed graph')
        
        # Create a new directed graph for augmentation
        self.g_augmented = self.g_working.copy()
        
        total_edges_added = 0
        straight_line_edges = 0
        
        for i, pair in enumerate(pairs):
            source, target = pair
            try:
                # Find shortest path between nodes
                try:
                    length, path = nx.single_source_dijkstra(self.g_working, source, target, weight='length')
                except nx.NetworkXNoPath:
                    log.error(f"No path found between nodes {source}-{target}")
                    continue
                
                log.debug(f'PAIR[{i}] nodes = ({source},{target}), length={length}, path={path}')
                
                # Create a linestring that follows the actual road path
                linestring = self.path_to_linestring(self.g_working, path)
                if linestring is None:
                    log.warning(f"Could not create linestring for path between nodes {source}-{target}, using straight line")
                    try:
                        source_coords = (self.g_working.nodes[source]['x'], self.g_working.nodes[source]['y'])
                        target_coords = (self.g_working.nodes[target]['x'], self.g_working.nodes[target]['y'])
                        linestring = shapely.geometry.LineString([source_coords, target_coords])
                        straight_line_edges += 1
                    except (KeyError, AttributeError) as e:
                        log.error(f"Cannot create straight line for edge {source}-{target}: {str(e)}")
                        continue
                
                # Create edge data with the path geometry
                edge_data = {
                    'length': length,
                    'augmented': True,
                    'path': path,
                    'geometry': linestring,
                    'from': source,
                    'to': target
                }
                
                # Add the directed edge
                self.g_augmented.add_edge(source, target, **edge_data)
                total_edges_added += 1
                
                if straight_line_edges > 0:
                    log.warning(f"Created straight line for edge {source}-{target} - STRAIGHT LINE WILL BE VISIBLE IN ROUTE")
                else:
                    log.info(f"Added augmented edge {source}-{target} with real road geometry")
                
            except Exception as e:
                log.error(f"Error creating augmented edge {source}-{target}: {str(e)}")
                continue
        
        # Log summary of edge additions
        if total_edges_added > 0:
            log.warning(f"Augmented edge summary:")
            log.warning(f"  - Total edges added: {total_edges_added}")
            log.warning(f"  - Edges using straight lines: {straight_line_edges}")
            log.warning(f"  - Edges using real geometry: {total_edges_added - straight_line_edges}")
            if straight_line_edges > 0:
                log.warning(f"  - {straight_line_edges} edges ({(straight_line_edges/total_edges_added)*100:.1f}%) are using straight lines!")
        
        # Verify all nodes are balanced
        unbalanced_nodes = []
        for node in self.g_augmented.nodes():
            in_degree = self.g_augmented.in_degree(node)
            out_degree = self.g_augmented.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            log.error("Graph is not balanced after augmentation:")
            for node, in_deg, out_deg in unbalanced_nodes:
                log.error(f"Node {node}: in={in_deg}, out={out_deg}")
            raise ValueError("Failed to maintain balance during augmentation")
        
        return

    ##
    ##
    def print_edges(self, g):

        for edge in g.edges:
            data = g.get_edge_data(*edge, 0)

            _osmid = ','.join(data.get('osmid')) if type(data.get('osmid')) == list else str(data.get('osmid'))
            _name = ','.join(data.get('name')) if type(data.get('name')) == list else str(data.get('name'))
            _highway = data.get('highway', '-')
            _surface = data.get('surface', '-')
            _oneway = data.get('oneway', '-')
            _access = data.get('access', '-')
            log.debug(f'{_osmid:10} {_name:30} {_highway:20} {_surface:10} {_oneway:10} {_access:10}')
            pass

    ##
    ##
    def find_connecting_edges(self, components):
        """Find the minimal set of edges needed to connect disconnected components."""
        log.info('Finding connecting edges between components')
        
        # Keep track of edges we need to add
        edges_to_add = []
        
        # Convert components to list for easier indexing
        component_list = list(components)
        
        # Create sets of nodes for each component for faster lookup
        component_sets = [set(comp) for comp in component_list]
        
        # Keep track of which components have been connected
        connected_components = {0}  # Start with the first component
        unconnected_components = set(range(1, len(component_list)))
        
        # Statistics for logging
        total_edges_added = 0
        straight_line_edges = 0
        
        while unconnected_components:
            min_path_length = float('inf')
            best_path = None
            best_component = None
            best_path_edges = None
            
            # Look for the shortest path connecting a connected component to an unconnected one
            for connected_idx in connected_components:
                connected_nodes = component_sets[connected_idx]
                
                for unconnected_idx in unconnected_components:
                    unconnected_nodes = component_sets[unconnected_idx]
                    
                    # Try to find paths between each pair of nodes
                    for u in connected_nodes:
                        for v in unconnected_nodes:
                            try:
                                # Use the original graph (with completed roads) to find the path
                                path_length, path = nx.single_source_dijkstra(self.g_original, u, v, weight='length')
                                
                                if path_length < min_path_length:
                                    min_path_length = path_length
                                    best_path = path
                                    best_component = unconnected_idx
                                    
                                    # Get all edges along this path
                                    path_edges = []
                                    for i in range(len(path) - 1):
                                        u_path, v_path = path[i], path[i + 1]
                                        edge_data = self.g_original.get_edge_data(u_path, v_path, 0)
                                        if edge_data:
                                            path_edges.append((u_path, v_path, dict(edge_data)))
                                    best_path_edges = path_edges
                            except nx.NetworkXNoPath:
                                continue
            
            if best_path is None:
                log.error("Could not find connecting path for all components")
                break
            
            # Add all edges along the best path we found
            if best_path_edges:
                for u, v, edge_data in best_path_edges:
                    total_edges_added += 1
                    # Mark this as a connecting edge
                    edge_data['connecting'] = True
                    
                    # Ensure we preserve the geometry from the original edge
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        try:
                            # Create a copy of the geometry to avoid modifying the original
                            edge_data['geometry'] = shapely.geometry.LineString(edge_data['geometry'].coords)
                            edges_to_add.append((u, v, edge_data))
                            log.info(f"Adding connecting edge {u}-{v} with real road geometry")
                        except Exception as e:
                            log.error(f"Error copying geometry for edge {u}-{v}: {str(e)}")
                            # Fall back to straight line if geometry copy fails
                            straight_line_edges += 1
                            self._add_straight_line_edge(u, v, edge_data, edges_to_add)
                    else:
                        # If no geometry, create a straight line
                        straight_line_edges += 1
                        self._add_straight_line_edge(u, v, edge_data, edges_to_add)
            
            # Mark the newly connected component
            connected_components.add(best_component)
            unconnected_components.remove(best_component)
        
        # Log summary of edge additions
        if total_edges_added > 0:
            log.warning(f"Edge connection summary:")
            log.warning(f"  - Total edges added: {total_edges_added}")
            log.warning(f"  - Edges using straight lines: {straight_line_edges}")
            log.warning(f"  - Edges using real geometry: {total_edges_added - straight_line_edges}")
            if straight_line_edges > 0:
                log.warning(f"  - {straight_line_edges} edges ({(straight_line_edges/total_edges_added)*100:.1f}%) are using straight lines!")
        
        return edges_to_add

    def _add_straight_line_edge(self, u, v, edge_data, edges_to_add):
        """Helper method to add a straight line edge between two nodes."""
        try:
            u_coords = (self.g_original.nodes[u]['x'], self.g_original.nodes[u]['y'])
            v_coords = (self.g_original.nodes[v]['x'], self.g_original.nodes[v]['y'])
            edge_data['geometry'] = shapely.geometry.LineString([u_coords, v_coords])
            edges_to_add.append((u, v, edge_data))
            log.warning(f"Created straight line geometry for edge {u}-{v} - STRAIGHT LINE WILL BE VISIBLE IN ROUTE")
        except Exception as e:
            log.error(f"Error creating straight line edge {u}-{v}: {str(e)}")

    ##
    ##
    def determine_nodes(self):
        """Determine nodes that need to be connected to balance the graph."""
        # Add debug check at the start
        self._debug_edge_attributes()
        
        edges_added = 0
        max_iterations = 100  # Prevent infinite loops
        iteration = 0
        
        log.info('Processing directed graph')

        # Store the original directed graph
        self.g_original = self.g.copy()
        
        # Create a working copy that we'll modify
        self.g_working = self.g.copy()
        
        # Ensure coordinates are preserved
        for node in self.g_working.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g_working.nodes[node]['x'] = x
                self.g_working.nodes[node]['y'] = y
        
        # Find weakly connected components (for directed graphs)
        components = list(nx.weakly_connected_components(self.g_working))
        if len(components) > 1:
            log.warning(f'Graph has {len(components)} disconnected components')
            # Log the size of each component
            for i, comp in enumerate(components):
                log.info(f'Component {i}: {len(comp)} nodes')
            
            # Find edges needed to connect components
            connecting_edges = self.find_connecting_edges(components)
            
            # Add the connecting edges to our graph
            for u, v, data in connecting_edges:
                # Add edge in both directions to ensure connectivity
                self.g_working.add_edge(u, v, **data)
                # Add reverse edge with same data but reversed geometry
                reverse_data = dict(data)
                if 'geometry' in reverse_data:
                    reverse_data['geometry'] = shapely.geometry.LineString(list(reverse_data['geometry'].coords)[::-1])
                self.g_working.add_edge(v, u, **reverse_data)
                log.debug(f'Added connecting edges {u}-{v} and {v}-{u} with geometry')
        
        # For each node, ensure in-degree equals out-degree (required for Eulerian circuit)
        nodes_balanced = 0
        edges_added = 0
        
        for node in self.g_working.nodes():
            in_degree = self.g_working.in_degree(node)
            out_degree = self.g_working.out_degree(node)
            
            if in_degree != out_degree:
                log.info(f"Node {node} has imbalanced degrees: in={in_degree}, out={out_degree}")
                # Add necessary edges to balance the node
                if in_degree > out_degree:
                    # Need more outgoing edges
                    for _ in range(in_degree - out_degree):
                        # Find a reachable node we can connect to
                        for target in self.g_working.nodes():
                            if target != node and not self.g_working.has_edge(node, target):
                                # Try to find a path to this node
                                try:
                                    path = nx.shortest_path(self.g_working, node, target, weight='length')
                                    # Create edge following this path
                                    self._add_path_as_edge(node, target, path)
                                    edges_added += 1
                                    break
                                except nx.NetworkXNoPath:
                                    continue
                elif out_degree > in_degree:
                    # Need more incoming edges
                    for _ in range(out_degree - in_degree):
                        # Find a node that can reach us
                        for source in self.g_working.nodes():
                            if source != node and not self.g_working.has_edge(source, node):
                                try:
                                    path = nx.shortest_path(self.g_working, source, node, weight='length')
                                    self._add_path_as_edge(source, node, path)
                                    edges_added += 1
                                    break
                                except nx.NetworkXNoPath:
                                    continue
                
                # Verify the node is now balanced
                new_in_degree = self.g_working.in_degree(node)
                new_out_degree = self.g_working.out_degree(node)
                if new_in_degree == new_out_degree:
                    nodes_balanced += 1
                else:
                    log.error(f"Failed to balance node {node}: in={new_in_degree}, out={new_out_degree}")
        
        log.info(f"Balanced {nodes_balanced} nodes by adding {edges_added} edges")
        
        # Create augmented graph
        self.g_augmented = self.g_working.copy()
        
        # Ensure coordinates are preserved in augmented graph
        for node in self.g_augmented.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g_augmented.nodes[node]['x'] = x
                self.g_augmented.nodes[node]['y'] = y
        
        # Verify the graph is balanced
        unbalanced_nodes = [(node, self.g_augmented.in_degree(node), self.g_augmented.out_degree(node))
                           for node in self.g_augmented.nodes()
                           if self.g_augmented.in_degree(node) != self.g_augmented.out_degree(node)]
        
        if unbalanced_nodes:
            log.error("Graph is still not balanced after processing:")
            for node, in_deg, out_deg in unbalanced_nodes:
                log.error(f"Node {node}: in={in_deg}, out={out_deg}")
            raise ValueError("Failed to create a balanced directed graph")
        
        return

    def _add_path_as_edge(self, source, target, path):
        """Helper method to add a new edge that follows an existing path."""
        if len(path) < 2:
            log.error(f"Path between {source}-{target} is too short")
            return False
        
        # Check if edge already exists
        if self.g_working.has_edge(source, target):
            log.info(f"Edge {source}-{target} already exists, skipping")
            return False
        
        # For all paths, calculate total length and collect coordinates
        length = 0
        coords = []
        
        # Collect all coordinates and calculate total length
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if not self.g_working.has_edge(u, v):
                log.error(f"Missing edge {u}-{v} in working graph")
                return False
            
            edge_data = self.g_working[u][v]
            if isinstance(edge_data, LineString):
                # Convert LineString to proper edge attributes
                geometry = edge_data
                length += geometry.length
                coords.extend(list(geometry.coords))
            else:
                # Normal dictionary attributes
                geometry = edge_data.get('geometry')
                if geometry is None:
                    # Create straight line geometry if missing
                    try:
                        u_coords = (self.g_working.nodes[u]['x'], self.g_working.nodes[u]['y'])
                        v_coords = (self.g_working.nodes[v]['x'], self.g_working.nodes[v]['y'])
                        geometry = LineString([u_coords, v_coords])
                        # Calculate Euclidean distance for length
                        segment_length = self.distance(u_coords, v_coords)
                        length += segment_length
                        coords.extend([u_coords, v_coords])
                        log.warning(f"Created straight line geometry for edge {u}-{v}")
                    except (KeyError, AttributeError) as e:
                        log.error(f"Cannot create straight line geometry for edge {u}-{v}: {str(e)}")
                        return False
                else:
                    length += edge_data.get('length', geometry.length)
                    coords.extend(list(geometry.coords))
        
        # Create the new edge with proper attributes
        if coords:
            # For MultiDiGraph, we need to pass the data as kwargs
            edge_data = {
                'geometry': LineString(coords),
                'length': length,
                'is_composite': True  # Mark this as a composite edge
            }
            self.g_working.add_edge(source, target, **edge_data)
            log.debug(f"Added edge {source}-{target} with length {length}")
            return True
        return False

    ##
    ##
    def optimise_dead_ends(self):
        """Optimize dead-end roads in a directed graph by adding return edges."""
        log.info('Optimizing dead-end roads in directed graph')
        
        # Find dead ends (nodes with total degree of 1)
        deadends = set()
        for node in self.g_working.nodes():
            in_degree = self.g_working.in_degree(node)
            out_degree = self.g_working.out_degree(node)
            if in_degree + out_degree == 1:
                deadends.add(node)
                log.info(f"Found dead end at node {node}: in={in_degree}, out={out_degree}")
        
        if not deadends:
            log.info("No dead ends found in graph")
            return
        
        log.info(f"Found {len(deadends)} dead ends to optimize")
        edges_added = 0
        
        for deadend in deadends:
            # Check incoming edges
            in_edges = list(self.g_working.in_edges(deadend, data=True))
            # Check outgoing edges
            out_edges = list(self.g_working.out_edges(deadend, data=True))
            
            if len(in_edges) + len(out_edges) != 1:
                log.error(f'Wrong number of edges for dead-end node {deadend}')
                continue
            
            # If we have an incoming edge, add a return edge
            if in_edges:
                source, target, data = in_edges[0]
                if not self.g_working.has_edge(target, source):
                    edge_data = dict(data)
                    edge_data['augmented'] = True
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        # Reverse the geometry for the return edge
                        edge_data['geometry'] = shapely.geometry.LineString(
                            list(edge_data['geometry'].coords)[::-1]
                        )
                    self.g_working.add_edge(target, source, **edge_data)
                    edges_added += 1
                    log.info(f"Added return edge for dead end: {target}->{source}")
            
            # If we have an outgoing edge, add a return edge
            if out_edges:
                source, target, data = out_edges[0]
                if not self.g_working.has_edge(target, source):
                    edge_data = dict(data)
                    edge_data['augmented'] = True
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        # Reverse the geometry for the return edge
                        edge_data['geometry'] = shapely.geometry.LineString(
                            list(edge_data['geometry'].coords)[::-1]
                        )
                    self.g_working.add_edge(target, source, **edge_data)
                    edges_added += 1
                    log.info(f"Added return edge for dead end: {target}->{source}")
        
        log.info(f"Added {edges_added} return edges for dead ends")
        
        # Verify the graph remains balanced
        unbalanced_nodes = []
        for node in self.g_working.nodes():
            in_degree = self.g_working.in_degree(node)
            out_degree = self.g_working.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            log.error("Graph is not balanced after dead end optimization:")
            for node, in_deg, out_deg in unbalanced_nodes:
                log.error(f"Node {node}: in={in_deg}, out={out_deg}")
            raise ValueError("Failed to maintain balance during dead end optimization")
        
        # Update augmented graph
        self.g_augmented = self.g_working.copy()
        
        # Preserve coordinates
        for node in self.g_augmented.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g_augmented.nodes[node]['x'] = x
                self.g_augmented.nodes[node]['y'] = y
        
        return

    ##
    ##
    def determine_combinations(self):
        """Determine combinations for balancing the directed graph."""
        log.info('Processing directed graph combinations')
        
        # Find nodes that need balancing
        need_incoming = []  # Nodes that need more incoming edges
        need_outgoing = []  # Nodes that need more outgoing edges
        
        for node in self.g_working.nodes():
            in_degree = self.g_working.in_degree(node)
            out_degree = self.g_working.out_degree(node)
            if in_degree < out_degree:
                need_incoming.append((node, out_degree - in_degree))
                log.info(f"Node {node} needs {out_degree - in_degree} incoming edges")
            elif out_degree < in_degree:
                need_outgoing.append((node, in_degree - out_degree))
                log.info(f"Node {node} needs {in_degree - out_degree} outgoing edges")
        
        log.info(f'Found {len(need_incoming)} nodes needing incoming edges')
        log.info(f'Found {len(need_outgoing)} nodes needing outgoing edges')
        
        # Add balancing edges
        edges_added = 0
        for source, out_needed in need_outgoing:
            for target, in_needed in need_incoming:
                if source != target and in_needed > 0 and out_needed > 0:
                    try:
                        # Find shortest path between nodes
                        path = nx.shortest_path(self.g_working, source, target, weight='length')
                        # Add edge following this path
                        self._add_path_as_edge(source, target, path)
                        edges_added += 1
                        in_needed -= 1
                        out_needed -= 1
                        log.info(f"Added balancing edge from {source} to {target}")
                    except nx.NetworkXNoPath:
                        log.warning(f"No path found between {source} and {target}")
                        continue
        
        log.info(f'Added {edges_added} balancing edges')
        
        # Verify balance
        unbalanced = []
        for node in self.g_working.nodes():
            in_degree = self.g_working.in_degree(node)
            out_degree = self.g_working.out_degree(node)
            if in_degree != out_degree:
                unbalanced.append((node, in_degree, out_degree))
                log.error(f'Node {node} remains unbalanced: in={in_degree}, out={out_degree}')
        
        if unbalanced:
            log.error(f'Graph still has {len(unbalanced)} unbalanced nodes:')
            for node, in_deg, out_deg in unbalanced:
                log.error(f'Node {node}: in={in_deg}, out={out_deg}')
            raise ValueError("Failed to balance the directed graph")
        
        # Create augmented graph
        self.g_augmented = self.g_working.copy()
        
        # Preserve coordinates
        for node in self.g_augmented.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g_augmented.nodes[node]['x'] = x
                self.g_augmented.nodes[node]['y'] = y
        
        # Verify graph is weakly connected
        if not nx.is_weakly_connected(self.g_augmented):
            components = list(nx.weakly_connected_components(self.g_augmented))
            raise ValueError(f"Graph is not connected after balancing. Found {len(components)} weakly connected components.")
        
        log.info("Successfully balanced directed graph")
        return

    ##
    ##
    def determine_circuit(self):
        """Determine the Eulerian circuit in the directed graph."""
        log.info('Starting to find Eulerian circuit in directed graph')
        
        # Get start node
        start_node = self.get_start_node(self.g_working, self.start)
        if start_node is None:
            # If no start node specified, use any node
            start_node = list(self.g_augmented.nodes())[0]
            log.info(f"Using node {start_node} as start point")
        
        # First verify the graph is balanced
        unbalanced_nodes = []
        for node in self.g_augmented.nodes():
            in_degree = self.g_augmented.in_degree(node)
            out_degree = self.g_augmented.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
                log.error(f"Node {node} has imbalanced degrees: in={in_degree}, out={out_degree}")
        
        if unbalanced_nodes:
            raise ValueError(f"Graph is not balanced. Found {len(unbalanced_nodes)} unbalanced nodes.")
        
        # Verify graph is weakly connected
        if not nx.is_weakly_connected(self.g_augmented):
            components = list(nx.weakly_connected_components(self.g_augmented))
            raise ValueError(f"Graph is not connected. Found {len(components)} weakly connected components.")
        
        # Find Eulerian circuit
        try:
            # For directed graphs, we use nx.eulerian_circuit directly
            self.euler_circuit = list(nx.eulerian_circuit(self.g_augmented, source=start_node))
            log.info(f"Found initial Eulerian circuit with {len(self.euler_circuit)} edges")
            
            # Verify all edges are included
            circuit_edges = set((u,v) for u,v in self.euler_circuit)
            all_edges = set(self.g_augmented.edges())
            missing_edges = all_edges - circuit_edges
            
            if missing_edges:
                log.error(f"Circuit is incomplete. Missing {len(missing_edges)} edges:")
                for edge in missing_edges:
                    log.error(f"Missing edge: {edge}")
                raise ValueError(f"Circuit is incomplete. Missing {len(missing_edges)} edges.")
            
            log.info("Successfully verified circuit includes all edges")
            return
            
        except nx.NetworkXError as e:
            log.error(f"Failed to find Eulerian circuit: {str(e)}")
            raise ValueError(f"Failed to find Eulerian circuit: {str(e)}")
        except Exception as e:
            log.error(f"Unexpected error finding Eulerian circuit: {str(e)}")
            raise

    ##
    ##
    def reverse_linestring(self, line):

        return shapely.geometry.LineString(line.coords[::-1])

    ##
    ##
    def distance(self, point1, point2):
        """Calculate the Euclidean distance between two points."""
        return math.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)

    ##
    ##
    def directional_linestring(self, edge, linestring):
        """
        Create a directional linestring for an edge, ensuring the direction matches the edge nodes.
        Returns None if the linestring cannot be created.
        """
        u, v = edge
        try:
            # Get node coordinates
            u_coords = (self.g.nodes[u]['x'], self.g.nodes[u]['y'])
            v_coords = (self.g.nodes[v]['x'], self.g.nodes[v]['y'])
        except (KeyError, AttributeError) as e:
            log.error(f"Missing node coordinates for edge {edge}: {str(e)}")
            return None

        try:
            # Get linestring coordinates
            coords = list(linestring.coords)
            if len(coords) < 2:
                log.error(f"Linestring for edge {edge} has fewer than 2 coordinates")
                return None

            # Get start and end points of the linestring
            start_point = coords[0]
            end_point = coords[-1]

            # Calculate distances to determine if we need to reverse
            start_dist_to_u = self.distance(start_point, u_coords)
            start_dist_to_v = self.distance(start_point, v_coords)
            end_dist_to_u = self.distance(end_point, u_coords)
            end_dist_to_v = self.distance(end_point, v_coords)

            # Use a small tolerance for coordinate matching
            tolerance = 1e-5

            # Check if linestring needs to be reversed
            needs_reverse = False
            if abs(start_dist_to_v) < tolerance and abs(end_dist_to_u) < tolerance:
                needs_reverse = True
            elif abs(start_dist_to_u) >= tolerance and abs(end_dist_to_v) >= tolerance:
                # If neither end matches exactly, use the closest points
                if start_dist_to_v + end_dist_to_u < start_dist_to_u + end_dist_to_v:
                    needs_reverse = True

            if needs_reverse:
                log.debug(f"Reversing linestring for edge {edge}")
                coords = coords[::-1]

            return coords

        except (AttributeError, IndexError) as e:
            log.error(f"Error processing linestring for edge {edge}: {str(e)}")
            return None

    ##
    ##
    def get_start_node(self, g, start_addr):

        if start_addr:
            (start_node, distance) = osmnx.distance.get_nearest_node(g, start_addr, return_dist=True)
            log.info('start_node=%s, distance=%s', start_node, distance)
        else:
            start_node = None
            pass

        return start_node

    ##
    ##
    def path_to_linestring(self, g, path):
        """Create a linestring that follows the actual road path between nodes."""
        if not path or len(path) < 2:
            log.error('Invalid path provided: must contain at least 2 nodes')
            return None

        # Keep track of straight line usage
        total_segments = len(path) - 1
        straight_line_segments = 0
        
        # Store all coordinates that will make up the final path
        all_coords = []
        
        # Process each pair of nodes in the path
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            
            # Try to get edge data in both directions
            edge_data = None
            for source, target in [(u, v), (v, u)]:
                temp_data = g.get_edge_data(source, target)
                if temp_data:
                    # Handle multiple edges between same nodes
                    if isinstance(temp_data, dict):
                        if 'geometry' in temp_data and temp_data['geometry'] is not None:
                            edge_data = temp_data
                            break
                    else:
                        # Find first edge with valid geometry
                        for key in temp_data:
                            if 'geometry' in temp_data[key] and temp_data[key]['geometry'] is not None:
                                edge_data = temp_data[key]
                                break
                    if edge_data:
                        break
            
            if edge_data and 'geometry' in edge_data and edge_data['geometry'] is not None:
                try:
                    # Get the geometry coordinates
                    geom = edge_data['geometry']
                    coords = list(geom.coords)
                    
                    # Validate coordinates
                    if not coords or len(coords) < 2:
                        raise ValueError(f"Invalid geometry coordinates for edge {u}-{v}")
                    
                    # Verify coordinate values
                    for x, y in coords:
                        if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                            raise ValueError(f"Invalid coordinate types in edge {u}-{v}")
                        if abs(x) > 180 or abs(y) > 90:
                            raise ValueError(f"Coordinate values out of range in edge {u}-{v}")
                    
                    # Check if we need to reverse the coordinates
                    if i > 0 and len(all_coords) > 0:
                        # Get the last point we added
                        last_point = all_coords[-1]
                        # Get distances to start and end of this segment
                        start_dist = self.distance(last_point, coords[0])
                        end_dist = self.distance(last_point, coords[-1])
                        # Reverse if end point is closer to our last point
                        if end_dist < start_dist:
                            coords = coords[::-1]
                    
                    # Verify continuity with previous segment
                    if i > 0 and len(all_coords) > 0:
                        gap = self.distance(all_coords[-1], coords[0])
                        if gap > 1e-5:  # Small tolerance for floating point comparison
                            log.warning(f"Gap detected in path at edge {u}-{v}: {gap}")
                            # Try to interpolate the gap
                            mid_point = ((all_coords[-1][0] + coords[0][0])/2, 
                                       (all_coords[-1][1] + coords[0][1])/2)
                            all_coords.append(mid_point)
                    
                    # Add all points except the first if this isn't the first segment
                    if i > 0 and len(all_coords) > 0:
                        all_coords.extend(coords[1:])
                    else:
                        all_coords.extend(coords)
                        
                    log.info(f"Using real road geometry for segment {u}-{v} with {len(coords)} points")
                    
                except Exception as e:
                    log.error(f"Error processing geometry for {u}-{v}: {str(e)}")
                    straight_line_segments += 1
                    self._add_straight_line_segment(g, u, v, all_coords, i)
            else:
                log.warning(f"No geometry found for {u}-{v}, using straight line")
                straight_line_segments += 1
                self._add_straight_line_segment(g, u, v, all_coords, i)
        
        if not all_coords:
            log.error("No coordinates generated for path")
            return None
            
        # Validate final path
        if len(all_coords) < 2:
            log.error("Path has fewer than 2 points")
            return None
            
        # Verify the total path length is reasonable
        try:
            total_length = sum(self.distance(all_coords[i], all_coords[i+1]) 
                             for i in range(len(all_coords)-1))
            straight_length = self.distance(all_coords[0], all_coords[-1])
            
            if total_length < straight_length * 0.9:
                log.error(f"Path length ({total_length}) is too short compared to straight line ({straight_length})")
                return None
            if total_length > straight_length * 3:
                log.error(f"Path length ({total_length}) is too long compared to straight line ({straight_length})")
                return None
        except Exception as e:
            log.error(f"Error validating path length: {str(e)}")
            return None
            
        # Log summary of geometry usage
        if straight_line_segments > 0:
            log.warning(f"Path geometry summary:")
            log.warning(f"  - Total segments: {total_segments}")
            log.warning(f"  - Segments using straight lines: {straight_line_segments}")
            log.warning(f"  - Segments using real geometry: {total_segments - straight_line_segments}")
            log.warning(f"  - {straight_line_segments} segments ({(straight_line_segments/total_segments)*100:.1f}%) are using straight lines!")
        
        try:
            linestring = shapely.geometry.LineString(all_coords)
            if not linestring.is_valid:
                log.error("Generated LineString is not valid")
                return None
            return linestring
        except Exception as e:
            log.error(f"Failed to create LineString from coordinates: {str(e)}")
            return None

    def _add_straight_line_segment(self, g, u, v, all_coords, segment_index):
        """Helper method to add a straight line segment between nodes."""
        try:
            # Get node coordinates
            u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
            v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
            
            # Validate coordinates
            for x, y in [u_coords, v_coords]:
                if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                    raise ValueError(f"Invalid coordinate types for node")
                if abs(x) > 180 or abs(y) > 90:
                    raise ValueError(f"Coordinate values out of range for node")
            
            # Add coordinates based on segment position
            if segment_index > 0 and len(all_coords) > 0:
                # Check for gaps and interpolate if needed
                gap = self.distance(all_coords[-1], u_coords)
                if gap > 1e-5:
                    mid_point = ((all_coords[-1][0] + u_coords[0])/2, 
                               (all_coords[-1][1] + u_coords[1])/2)
                    all_coords.append(mid_point)
                all_coords.append(u_coords)
                all_coords.append(v_coords)
            else:
                all_coords.extend([u_coords, v_coords])
                
            log.warning(f"Added straight line segment for {u}-{v}")
            
        except Exception as e:
            log.error(f"Error creating straight line segment {u}-{v}: {str(e)}")

    ##
    ##
    def prune(self):

        # eliminate edges with unnamed tracks.  At least where I live,
        # these tend to be 4wd tracks that require a mountain bike to
        # navigate.  probably need to do a better fitler that looks at
        # surface type and other aspects.

        remove_types = ('track', 'path')

        removeset = set()
        for edge in self.g.edges:
            data = self.g.get_edge_data(*edge)

            if data.get('highway') in remove_types and data.get('name') is None:
                log.debug('removing edge %s, %s', edge, data)
                removeset.add(edge)
                pass

            if data.get('highway') in ('cycleway',):
                log.debug('removing edge %s, %s', edge, data)
                removeset.add(edge)
                pass
            pass

        for edge in removeset:
            self.g.remove_edge(*edge)
            pass

        # this removes the isolated nodes orphaned from the removed
        # edges above.  It does not solve the problem of a
        # non-connected graph (ie, nodes and edges in a blob that
        # aren't reachable to other parts of the graph)

        self.g = osmnx.utils_graph.remove_isolated_nodes(self.g)
        return

    ##
    ##
    def save_fig(self):

        filename = f'burb_nodes_{self.name}.svg'

        log.info('saving SVG node file as %s', filename)

        nc = ['red' if node in self.odd_nodes else 'blue' for node in self.g.nodes() ]

        fig, ax = osmnx.plot_graph(self.g, show=False, save=True, node_color=nc, filepath=filename)

        return

    ##
    ##
    def load(self, options):
        log.info('Fetching OSM data bounded by polygon')
        # Get directed graph from OSM
        self.g = osmnx.graph_from_polygon(self.region, network_type='drive', simplify=False, 
                                        custom_filter=self.custom_filter, retain_all=True)
        
        # Ensure we have a directed graph
        if not isinstance(self.g, nx.DiGraph):
            log.warning("Converting graph to directed type")
            self.g = nx.DiGraph(self.g)
        
        # Store original graph immediately after creation
        self.g_original = self.g.copy()
        
        # Get nodes and edges as GeoDataFrames with explicit geometry
        nodes, edges = osmnx.utils_graph.graph_to_gdfs(self.g, nodes=True, edges=True, 
                                                      node_geometry=True, fill_edge_geometry=True)
        
        # Ensure we have node coordinates
        if 'geometry' not in nodes.columns:
            log.error("Node geometry is missing from GeoDataFrame")
            raise ValueError("Node geometry is missing")
        
        # Create a dictionary to store node coordinates
        self.node_coords = {}
        
        # Extract coordinates from node geometries and store them
        for node_id, node_data in nodes.iterrows():
            try:
                point = node_data['geometry']
                x, y = point.x, point.y
                self.node_coords[node_id] = (x, y)
                self.g.nodes[node_id]['x'] = x
                self.g.nodes[node_id]['y'] = y
            except Exception as e:
                log.error(f"Could not extract coordinates for node {node_id}: {str(e)}")
        
        # Log coordinate statistics
        nodes_with_coords = sum(1 for n in self.g.nodes if 'x' in self.g.nodes[n] and 'y' in self.g.nodes[n])
        log.info(f"Node coordinate statistics:")
        log.info(f"  - Total nodes: {len(self.g.nodes)}")
        log.info(f"  - Nodes with coordinates: {nodes_with_coords}")
        
        if nodes_with_coords < len(self.g.nodes):
            log.warning(f"Missing coordinates for {len(self.g.nodes) - nodes_with_coords} nodes")
        
        log.debug('original g=%s, g=%s', self.g, type(self.g))
        log.info('original nodes=%s, edges=%s', self.g.order(), self.g.size())
        
        # Create working copy of the graph
        self.g_working = self.g.copy()
        
        # If we have completed roads and want to exclude them
        if hasattr(self, 'completed_area') and not self.completed_area.is_empty and options.exclude_completed:
            log.info('Removing completed roads from the network')
            
            # Create a buffer around the completed area
            completed_area_buffer = self.completed_area.buffer(0.00015)  # ~15 meter buffer
            if not completed_area_buffer.is_valid:
                completed_area_buffer = completed_area_buffer.buffer(0)
            
            log.info(f"Created completed area buffer: valid={completed_area_buffer.is_valid}, empty={completed_area_buffer.is_empty}, area={completed_area_buffer.area}")
            
            edges_processed = 0
            edges_removed = 0
            total_edges = len(self.g.edges())
            
            # Keep track of edges to remove and potential connector edges
            edges_to_remove = []
            connector_edges = []
            
            # Process edges in both directions
            for u, v, data in list(self.g.edges(data=True)):
                edges_processed += 1
                
                try:
                    # Get edge geometry
                    if 'geometry' in data and data['geometry'] is not None:
                        edge_geom = data['geometry']
                    else:
                        # Create straight line geometry if none exists
                        try:
                            u_coords = self.node_coords[u]
                            v_coords = self.node_coords[v]
                            edge_geom = shapely.geometry.LineString([u_coords, v_coords])
                        except (KeyError, AttributeError) as e:
                            log.error(f"Cannot create geometry for edge {u}-{v}: {str(e)}")
                            continue
                    
                    # Create buffer around edge
                    edge_buffer = edge_geom.buffer(0.00005)  # ~5 meter buffer
                    if not edge_buffer.is_valid:
                        edge_buffer = edge_buffer.buffer(0)
                    
                    if edge_buffer.intersects(completed_area_buffer):
                        intersection = edge_buffer.intersection(completed_area_buffer)
                        if not intersection.is_valid:
                            intersection = intersection.buffer(0)
                        
                        intersection_area = intersection.area if hasattr(intersection, 'area') else 0
                        edge_area = edge_buffer.area if edge_buffer.area > 0 else 1e-10
                        overlap_ratio = intersection_area / edge_area
                        
                        # If more than 30% completed
                        if overlap_ratio > 0.3:
                            # If we're allowing connectors, store it as a potential connector
                            if options.allow_completed_connectors:
                                connector_edges.append((u, v, dict(data)))
                                log.debug(f"Marking edge {u}-{v} as potential connector (overlap: {overlap_ratio:.2%})")
                            # Otherwise, mark for removal
                            else:
                                edges_to_remove.append((u, v))
                                edges_removed += 1
                                log.debug(f"Marking edge {u}-{v} for removal (overlap: {overlap_ratio:.2%})")
                    
                    # Only log progress every 500 edges
                    if edges_processed % 500 == 0:
                        log.info(f"Processed {edges_processed}/{total_edges} edges, marked {edges_removed} completed edges for removal")
                        
                except Exception as e:
                    log.error(f"Error processing edge {u}-{v}: {str(e)}")
                    continue
            
            # Remove the completed edges
            edges_actually_removed = 0
            for u, v in edges_to_remove:
                if self.g.has_edge(u, v):
                    self.g.remove_edge(u, v)
                    edges_actually_removed += 1
            
            # If we're allowing connectors, only add them back if needed to maintain connectivity
            if options.allow_completed_connectors and connector_edges:
                # Find weakly connected components after removing completed edges
                components = list(nx.weakly_connected_components(self.g))
                if len(components) > 1:
                    log.info(f"Graph has {len(components)} disconnected components after removing completed edges")
                    
                    # Find minimal set of connector edges needed
                    connecting_edges = self.find_connecting_edges(components)
                    
                    # Add back only the necessary connector edges
                    connectors_added = 0
                    for u, v, data in connecting_edges:
                        self.g.add_edge(u, v, **data)
                        connectors_added += 1
                    
                    log.info(f"Added {connectors_added} completed roads back as connectors")
            
            # Remove any isolated nodes that resulted from edge removal
            original_node_count = self.g.number_of_nodes()
            self.g = osmnx.utils_graph.remove_isolated_nodes(self.g)
            
            # Restore coordinates for remaining nodes
            for node in self.g.nodes():
                if node in self.node_coords:
                    x, y = self.node_coords[node]
                    self.g.nodes[node]['x'] = x
                    self.g.nodes[node]['y'] = y
            
            nodes_removed = original_node_count - self.g.number_of_nodes()
            
            # Ensure coordinates are preserved after removing isolated nodes
            nodes_with_coords = sum(1 for n in self.g.nodes if 'x' in self.g.nodes[n] and 'y' in self.g.nodes[n])
            log.warning(f"Completed roads removal summary:")
            log.warning(f"  - Edges processed: {edges_processed}")
            log.warning(f"  - Edges marked for removal: {edges_removed}")
            log.warning(f"  - Edges actually removed: {edges_actually_removed}")
            if options.allow_completed_connectors:
                log.warning(f"  - Completed edges kept as connectors: {len(connector_edges)}")
            log.warning(f"  - Nodes removed: {nodes_removed}")
            log.warning(f"  - Remaining nodes: {len(self.g.nodes)}")
            log.warning(f"  - Nodes with coordinates: {nodes_with_coords}")
            
            if nodes_with_coords < len(self.g.nodes):
                log.warning(f"Missing coordinates for {len(self.g.nodes) - nodes_with_coords} nodes after processing")

        if options.simplify:
            log.info('simplifying graph')
            self.g = osmnx.simplification.simplify_graph(self.g, strict=False, remove_rings=False)
            
            # Restore coordinates after simplification
            for node in self.g.nodes():
                if node in self.node_coords:
                    x, y = self.node_coords[node]
                    self.g.nodes[node]['x'] = x
                    self.g.nodes[node]['y'] = y
            
            # Ensure coordinates are preserved after simplification
            nodes_with_coords = sum(1 for n in self.g.nodes if 'x' in self.g.nodes[n] and 'y' in self.g.nodes[n])
            log.info(f"After simplification:")
            log.info(f"  - Remaining nodes: {len(self.g.nodes)}")
            log.info(f"  - Nodes with coordinates: {nodes_with_coords}")
        
        # Update working copy after all modifications
        self.g_working = self.g.copy()
        
        return

    ##
    ##
    def load_shapefile(self, filename):

        df = geopandas.read_file(filename)
        log.info('df=%s', df)
        log.info('df.crs=%s', df.crs)

        return df

    ##
    ##
    def add_shapefile_region(self, name):

        df = self.shapefile_df
        key = self.shapefile_key

        suburb = df[df[key] == value]
        log.info('suburb=%s', suburb)
        suburb = suburb.to_crs(epsg=4326)
        log.info('suburb=%s', suburb)

        polygon = suburb['geometry'].values[0]

        return polygon

    ##
    ##
    def create_gpx_polygon(self, polygon):

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'boundary {self.name}'
        gpx.author_name = 'optiburb'
        gpx.creator = 'experimental burbing'
        gpx.description = f'experimental burbing boundary for {self.name}'

        track = gpxpy.gpx.GPXTrack()
        track.name = f'burb bound {self.name}'

        filename = f'burb_polygon_{self.name}.gpx'

        log.info('saving suburb boundary - %s', filename)

        # XXX - add colour?

        #xmlns:gpxx="http://www.garmin.com/xmlschemas/GpxExtensions/v3"
        #track.extensions =
        #<extensions>
        #  <gpxx:TrackExtension>
        #    <gpxx:DisplayColor>Red</gpxx:DisplayColor>
        #  </gpxx:TrackExtension>
        #</extensions>

        gpx.tracks.append(track)

        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)

        for x, y in polygon.exterior.coords:
            segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=y, longitude=x))
            pass

        data = gpx.to_xml()
        with open(filename, 'w') as f:
            f.write(data)

        return

    ##
    ##
    def create_gpx_track(self, g, edges, simplify=False):
        """Create a GPX track with direction indicators."""
        stats_distance = 0.0
        stats_backtrack = 0.0
        stats_deadends = 0
        total_direction_markers = 0

        log.info('Creating GPX track with direction indicators')
        log.info(f'Number of edges to process: {len(edges)}')

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'burb {self.name}'
        gpx.author_name = 'optiburb'
        gpx.creator = 'experimental burbing'
        gpx.description = f'experimental burbing route for {self.name} (with direction indicators)'

        # Add style information as keywords
        gpx.keywords = 'directed route,one-way streets'

        track = gpxpy.gpx.GPXTrack()
        track.name = f'burb trk {self.name}'
        track.type = 'directed'  # Custom type to indicate this is a directed route
        gpx.tracks.append(track)

        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)

        i = 1
        arrow_interval = 3  # Add direction arrow more frequently (every 3 points)
        log.info(f'Using arrow interval of {arrow_interval} points')

        for n, edge in enumerate(edges):
            u, v = edge
            edge_data = g.get_edge_data(*edge, 0)

            log.debug('EDGE [%d] - edge=%s, data=%s', n, edge, edge_data)

            if edge_data is None:
                log.warning('null data for edge %s', edge)
                try:
                    u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                    v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                    # Add points with direction indicator
                    markers_added = self._add_track_points(segment, [u_coords, v_coords], i, arrow_interval)
                    total_direction_markers += markers_added if markers_added else 0
                    i += 2
                except (KeyError, AttributeError) as e:
                    log.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                continue

            linestring = edge_data.get('geometry')
            augmented = edge_data.get('augmented')
            stats_distance += edge_data.get('length', 0)

            log.debug(' leg [%d] -> %s (%s,%s,%s,%s,%s)', n, edge_data.get('name', ''), 
                     edge_data.get('highway', ''), edge_data.get('surface', ''), 
                     edge_data.get('oneway', ''), edge_data.get('access', ''), 
                     edge_data.get('length', 0))

            coords_to_use = None
            if linestring:
                directional_linestring = self.directional_linestring(edge, linestring)
                if directional_linestring:
                    coords_to_use = directional_linestring
                    log.debug(f'Using directional linestring with {len(coords_to_use)} points')

            if coords_to_use is None:
                try:
                    u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                    v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                    coords_to_use = [u_coords, v_coords]
                    log.debug(f"Using straight line for edge {edge}")
                except (KeyError, AttributeError) as e:
                    log.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                    continue

            # Add points with direction indicators
            markers_added = self._add_track_points(segment, coords_to_use, i, arrow_interval)
            total_direction_markers += markers_added if markers_added else 0
            i += len(coords_to_use)

            if edge_data.get('augmented', False):
                stats_backtrack += edge_data.get('length', 0)

        log.info('total distance = %.2fkm', stats_distance/1000.0)
        log.info('backtrack distance = %.2fkm', stats_backtrack/1000.0)
        log.info(f'Total direction markers added to GPX: {total_direction_markers}')
        
        if simplify:
            log.info('simplifying GPX')
            gpx.simplify()

        data = gpx.to_xml()
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'burb_track_{self.name}_{timestamp}.gpx'

        log.info('Saving GPX track to %s', filename)
        with open(filename, 'w') as f:
            f.write(data)

        return filename

    def _add_track_points(self, segment, coords, start_index, arrow_interval):
        """Add track points with direction indicators at specified intervals."""
        direction_markers_added = 0
        log.info(f'Adding track points for segment starting at index {start_index}, interval={arrow_interval}')
        log.info(f'Number of coordinates to process: {len(coords)}')
        
        # For very short segments (2 points), always add a direction marker at the first point
        is_short_segment = len(coords) == 2
        
        for i, (lon, lat) in enumerate(coords):
            point = gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon)
            
            # Add direction indicator if:
            # 1. For short segments (2 points): at the first point
            # 2. For longer segments: at regular intervals within the segment
            should_add_marker = (
                (is_short_segment and i == 0) or  # First point of short segment
                (not is_short_segment and i % arrow_interval == 0 and i < len(coords) - 1)  # Regular interval within segment
            )
            
            if should_add_marker:
                next_lon, next_lat = coords[i + 1]
                bearing = self._calculate_bearing(lat, lon, next_lat, next_lon)
                
                # Set point attributes for direction marker
                point.type = 'direction'  # Changed to match what the frontend expects
                point.symbol = '➜'  # Set a larger arrow symbol
                point.comment = str(round(bearing, 1))  # Store bearing directly in comment
                
                direction_markers_added += 1
                log.info(f'Added direction marker at point {start_index + i}: bearing={bearing}°, coords=({lat}, {lon})')
            
            segment.points.append(point)
        
        log.info(f'Added {direction_markers_added} direction markers in this segment')
        if direction_markers_added == 0:
            log.warning('No direction markers were added in this segment')
            log.warning(f'Segment details: start_index={start_index}, coords={len(coords)}, interval={arrow_interval}')
        
        return direction_markers_added

    def _calculate_bearing(self, lat1, lon1, lat2, lon2):
        """Calculate the bearing between two points in degrees."""
        import math
        
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        
        # Calculate bearing
        d_lon = lon2 - lon1
        y = math.sin(d_lon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
        bearing = math.atan2(y, x)
        
        # Convert to degrees
        bearing = math.degrees(bearing)
        
        # Normalize to 0-360
        return (bearing + 360) % 360

    def _find_road_following_path(self, g, source, target, max_attempts=3):
        """Find a path between nodes that follows the road network with valid geometry."""
        log.info(f"Finding road-following path from {source} to {target}")
        
        # Get node coordinates
        try:
            source_coords = (g.nodes[source]['x'], g.nodes[source]['y'])
            target_coords = (g.nodes[target]['x'], g.nodes[target]['y'])
            straight_line_dist = self.distance(source_coords, target_coords)
            log.info(f"Straight line distance: {straight_line_dist}")
        except KeyError as e:
            log.error(f"Missing coordinates for nodes: {str(e)}")
            return None
        
        # Try different path-finding strategies
        for attempt in range(max_attempts):
            try:
                if attempt == 0:
                    # First try: shortest path by length
                    path = nx.shortest_path(g, source, target, weight='length')
                    log.info(f"Attempt {attempt + 1}: Found shortest path by length with {len(path)} nodes")
                elif attempt == 1:
                    # Second try: shortest path by hops (number of edges)
                    path = nx.shortest_path(g, source, target)
                    log.info(f"Attempt {attempt + 1}: Found shortest path by hops with {len(path)} nodes")
                else:
                    # Last try: Dijkstra with modified weights to prefer roads with geometry
                    temp_graph = g.copy()
                    for u, v, data in temp_graph.edges(data=True):
                        # Penalize edges without geometry
                        if 'geometry' not in data or data['geometry'] is None:
                            data['modified_length'] = data.get('length', 1) * 2
                        else:
                            data['modified_length'] = data.get('length', 1)
                    path = nx.shortest_path(temp_graph, source, target, weight='modified_length')
                    log.info(f"Attempt {attempt + 1}: Found path with geometry preference, {len(path)} nodes")
                
                # Validate the path
                total_length = 0
                has_valid_geometry = True
                prev_end = None
                
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    
                    # Try both directions for the edge
                    edge_data = g.get_edge_data(u, v) or g.get_edge_data(v, u)
                    if not edge_data:
                        log.error(f"No edge data found for {u}-{v}")
                        has_valid_geometry = False
                        break
                    
                    # Handle multiple edges between same nodes
                    if not isinstance(edge_data, dict):
                        # Find first edge with valid geometry
                        for key in edge_data:
                            if 'geometry' in edge_data[key] and edge_data[key]['geometry'] is not None:
                                edge_data = edge_data[key]
                                break
                        if not isinstance(edge_data, dict):
                            log.error(f"No valid edge data found for {u}-{v}")
                            has_valid_geometry = False
                            break
                    
                    # Check geometry
                    if 'geometry' not in edge_data or edge_data['geometry'] is None:
                        # Try to create straight line geometry
                        try:
                            u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                            v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                            edge_data['geometry'] = shapely.geometry.LineString([u_coords, v_coords])
                            log.warning(f"Created straight line geometry for edge {u}-{v}")
                        except (KeyError, AttributeError) as e:
                            log.error(f"Cannot create straight line geometry for edge {u}-{v}: {str(e)}")
                            has_valid_geometry = False
                            break
                    
                    # Check geometry continuity
                    curr_coords = list(edge_data['geometry'].coords)
                    if not curr_coords or len(curr_coords) < 2:
                        log.error(f"Invalid geometry coordinates for edge {u}-{v}")
                        has_valid_geometry = False
                        break
                    
                    # Check if we need to reverse coordinates
                    if g.get_edge_data(v, u) == edge_data:
                        curr_coords = curr_coords[::-1]
                    
                    # Check continuity with previous segment
                    if prev_end is not None:
                        gap = self.distance(prev_end, curr_coords[0])
                        if gap > 1e-5:
                            log.error(f"Discontinuity in path at edge {u}-{v}: gap={gap}")
                            has_valid_geometry = False
                            break
                    
                    prev_end = curr_coords[-1]
                    total_length += edge_data.get('length', 0)
                
                if not has_valid_geometry:
                    log.warning(f"Attempt {attempt + 1}: Path has invalid geometry")
                    continue
                
                # Verify total length is reasonable
                if straight_line_dist > 1e-5:  # Only check if points are not too close
                    if total_length < straight_line_dist * 0.9:
                        log.error(f"Path length ({total_length}) is too short compared to straight line distance ({straight_line_dist})")
                        continue
                    if total_length > straight_line_dist * 3:
                        log.error(f"Path length ({total_length}) is too long compared to straight line distance ({straight_line_dist})")
                        continue
                
                log.info(f"Found valid path with length {total_length}")
                return path
                
            except nx.NetworkXNoPath:
                log.error(f"No path found on attempt {attempt + 1}")
                continue
            except Exception as e:
                log.error(f"Error finding path on attempt {attempt + 1}: {str(e)}")
                continue
        
        log.error(f"Failed to find valid path after {max_attempts} attempts")
        return None

    def _debug_edge_attributes(self):
        """Debug helper to identify edges with incorrect attribute format."""
        for u, v, data in self.g_working.edges(data=True):
            if not isinstance(data, dict):
                log.error(f"Edge {u}-{v} has non-dictionary attributes: {type(data)}")
                # Convert LineString to proper edge attributes if needed
                if isinstance(data, LineString):
                    self.g_working[u][v] = {
                        'geometry': data,
                        'length': data.length
                    }
                    log.info(f"Fixed edge {u}-{v} attributes")

    def _are_roads_parallel(self, line1, line2, max_distance=0.0003):  # roughly 30 meters
        """Check if two roads are parallel and close to each other."""
        try:
            # Buffer both lines slightly and check if they overlap
            buffer1 = line1.buffer(max_distance/2)
            buffer2 = line2.buffer(max_distance/2)
            
            # Check if the buffers overlap significantly
            if not buffer1.intersects(buffer2):
                return False
                
            # Calculate overlap ratio
            intersection = buffer1.intersection(buffer2)
            if not intersection.is_valid:
                intersection = intersection.buffer(0)
            
            # Calculate overlap ratio relative to the smaller buffer
            overlap_ratio = intersection.area / min(buffer1.area, buffer2.area)
            if overlap_ratio < 0.3:  # Require at least 30% overlap
                return False
            
            # Get evenly spaced points along both lines for better bearing comparison
            num_points = 4  # Check bearings at more points along the lines
            points1 = [line1.interpolate(i/float(num_points-1), normalized=True) for i in range(num_points)]
            points2 = [line2.interpolate(i/float(num_points-1), normalized=True) for i in range(num_points)]
            
            # Compare bearings at multiple points
            max_bearing_diff = 0
            for i in range(num_points-1):
                # Calculate bearing for line1
                bearing1 = self._calculate_bearing(
                    points1[i].y, points1[i].x,
                    points1[i+1].y, points1[i+1].x
                )
                
                # Calculate bearing for line2
                bearing2 = self._calculate_bearing(
                    points2[i].y, points2[i].x,
                    points2[i+1].y, points2[i+1].x
                )
                
                # Calculate bearing difference
                bearing_diff = abs((bearing1 - bearing2 + 180) % 360 - 180)
                max_bearing_diff = max(max_bearing_diff, bearing_diff)
            
            # Allow more tolerance for parallel roads (30 degrees)
            # Also accept nearly parallel roads (within 30 degrees of 180)
            return max_bearing_diff < 30 or abs(max_bearing_diff - 180) < 30
            
        except Exception as e:
            log.warning(f"Error checking parallel roads: {str(e)}")
            return False

    def merge_parallel_roads(self):
        """Identify and merge parallel bidirectional roads."""
        if not self.g:
            return
        
        log.info("Identifying parallel bidirectional roads...")
        edges_to_remove = set()
        edges_processed = set()
        
        # Get all edges with geometry
        edges = [(u, v, d) for u, v, d in self.g.edges(data=True) if 'geometry' in d]
        
        # First pass: identify parallel roads that are part of the same named road
        for i, (u1, v1, d1) in enumerate(edges):
            if (u1, v1) in edges_processed:
                continue
                
            geometry1 = d1['geometry']
            if not isinstance(geometry1, LineString):
                continue
            
            road_name1 = d1.get('name')
            
            # Look for parallel roads
            for u2, v2, d2 in edges[i+1:]:
                if (u2, v2) in edges_processed:
                    continue
                    
                geometry2 = d2['geometry']
                if not isinstance(geometry2, LineString):
                    continue
                
                road_name2 = d2.get('name')
                
                # Prioritize merging roads with the same name
                if road_name1 and road_name1 == road_name2:
                    if self._are_roads_parallel(geometry1, geometry2, max_distance=0.0004):  # 40 meters for same-named roads
                        self._merge_road_pair(u1, v1, u2, v2, d1, d2, edges_to_remove, edges_processed)
        
        # Second pass: identify other parallel roads (like parking lots, service roads)
        for i, (u1, v1, d1) in enumerate(edges):
            if (u1, v1) in edges_processed:
                continue
                
            geometry1 = d1['geometry']
            if not isinstance(geometry1, LineString):
                continue
            
            highway_type1 = d1.get('highway')
            
            # Look for parallel roads
            for u2, v2, d2 in edges[i+1:]:
                if (u2, v2) in edges_processed:
                    continue
                    
                geometry2 = d2['geometry']
                if not isinstance(geometry2, LineString):
                    continue
                
                highway_type2 = d2.get('highway')
                
                # Use different distance thresholds based on road types
                max_distance = 0.0003  # default 30 meters
                if highway_type1 == highway_type2 == 'service':
                    max_distance = 0.0004  # 40 meters for service roads
                elif 'parking' in str(d1.get('service', '')) or 'parking' in str(d2.get('service', '')):
                    max_distance = 0.0005  # 50 meters for parking areas
                
                if self._are_roads_parallel(geometry1, geometry2, max_distance=max_distance):
                    self._merge_road_pair(u1, v1, u2, v2, d1, d2, edges_to_remove, edges_processed)
        
        # Remove the redundant edges
        for u, v in edges_to_remove:
            if self.g.has_edge(u, v):
                self.g.remove_edge(u, v)
        
        log.info(f"Merged {len(edges_to_remove)//2} pairs of parallel bidirectional roads")

    def _merge_road_pair(self, u1, v1, u2, v2, d1, d2, edges_to_remove, edges_processed):
        """Helper method to merge a pair of parallel roads."""
        # Check if they form a bidirectional pair
        if (self.g.has_edge(v1, u1) and self.g.has_edge(v2, u2)) or \
           (self.g.has_edge(u2, u1) and self.g.has_edge(v2, v1)):
            # Merge the roads by keeping one direction and removing the other
            edges_to_remove.add((u2, v2))
            edges_to_remove.add((v2, u2))
            edges_processed.add((u1, v1))
            edges_processed.add((v1, u1))
            
            # Update the kept edge to indicate it's bidirectional
            self.g[u1][v1]['bidirectional'] = True
            
            # Merge road names if available
            names = set()
            if 'name' in d1 and d1['name']:
                names.add(d1['name'])
            if 'name' in d2 and d2['name']:
                names.add(d2['name'])
            if names:
                self.g[u1][v1]['merged_names'] = names
            
            # Mark the type of merge
            if 'parking' in str(d1.get('service', '')) or 'parking' in str(d2.get('service', '')):
                self.g[u1][v1]['merge_type'] = 'parking'
            elif d1.get('highway') == d2.get('highway') == 'service':
                self.g[u1][v1]['merge_type'] = 'service'
            else:
                self.g[u1][v1]['merge_type'] = 'parallel'

    pass

##
##
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Optimum Suburb Route Generator')
    parser.add_argument('names', type=str, nargs=argparse.REMAINDER, help='suburb names with state, country, etc')
    parser.add_argument('--debug', type=str, default='info', help='debug level debug, info, warn, etc')
    parser.add_argument('--start', type=str, help='optional starting address')
    parser.add_argument('--prune', default=False, action='store_true', help='prune unnamed gravel tracks')
    parser.add_argument('--simplify', default=False, action='store_true', help='simplify OSM nodes on load')
    parser.add_argument('--simplify-gpx', dest='simplify_gpx', default=True, action='store_true', help='reduce GPX points')
    parser.add_argument('--complex-gpx', dest='simplify_gpx', action='store_false', help='leave all the OSM points in the GPX output')
    parser.add_argument('--select', type=int, default=1, help='select the nth item from the search results. a truely awful hack because i cant work out how to search for administrative boundaries.')
    parser.add_argument('--shapefile', type=str, default=None, help='filename of shapefile to load localities, comma separated by the column to match on')
    parser.add_argument('--buffer', type=int, dest='buffer', default=20, help='buffer distsance around polygon')
    parser.add_argument('--save-fig', default=False, action='store_true', help='save an SVG image of the nodes and edges')
    parser.add_argument('--save-boundary', default=False, action='store_true', help='save a GPX file of the suburb boundary')
    parser.add_argument('--feature-deadend', default=False, action='store_true', help='experimental feature to optimised deadends in solution')

    args = parser.parse_args()

    log.setLevel(logging.getLevelName(args.debug.upper()))

    log.debug('called with args - %s', args)

    start_time = datetime.datetime.now()

    burbing = Burbing()

    if not args.names:
        parser.print_help()
        sys.exit(1)
        pass

    if args.shapefile:

        filename, key = args.shapefile.split(',')

        log.info('shapefile=%s, key=%s', filename, key)

        shapefile = burbing.load_shapefile(filename)

        for name in args.names:
            polygon = burbing.get_shapefile_polygon(shapefile, key, name)
            burbing.add_polygon(polygon, name)
            pass
        pass

    else:

        for name in args.names:

            polygon = burbing.get_osm_polygon(name, args.select, args.buffer)
            burbing.add_polygon(polygon, name)
            pass
        pass

    if args.save_boundary:
        burbing.create_gpx_polygon(burbing.region)
        pass

    if args.start:
        burbing.set_start_location(args.start)
        pass

    burbing.load(args)

    if args.prune:
        burbing.prune()
        pass

    burbing.determine_nodes()

    if args.feature_deadend:
        burbing.optimise_dead_ends()
        pass

    if args.save_fig:
        burbing.save_fig()
        pass

    burbing.determine_combinations()

    burbing.determine_circuit()

    burbing.create_gpx_track(burbing.g_augmented, burbing.euler_circuit, args.simplify_gpx)

    end_time = datetime.datetime.now()

    log.info('elapsed time = %s', end_time - start_time)

    pass

