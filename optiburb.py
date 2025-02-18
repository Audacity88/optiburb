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

# Configure logging to always show warnings and errors
logging.basicConfig(
    format='%(asctime)-15s %(filename)s:%(funcName)s:%(lineno)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.WARNING,
    force=True
)
log = logging.getLogger(__name__)

class Burbing:

    WARNING = '''WARNING - this program does not consider the direction of one-way roads or other roads that may be not suitable for your mode of transport. You must confirm the path safe for yourself'''

    def __init__(self):

        self.g = None

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
        pairs = list(itertools.combinations(nodes, 2))
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
        """Create new edges between odd node pairs using actual road paths."""
        log.info('pre augmentation eulerian=%s', nx.is_eulerian(self.g_augmented))
        
        total_edges_added = 0
        straight_line_edges = 0
        
        for i, pair in enumerate(pairs):
            a, b = pair
            try:
                # Get the shortest path between the nodes
                length, path = nx.single_source_dijkstra(self.g, a, b, weight='length')
                
                log.debug('PAIR[%s] nodes = (%s,%s), length=%s, path=%s', i, a, b, length, path)
                
                # Create a linestring that follows the actual road path
                linestring = self.path_to_linestring(self.g, path)
                if linestring is None:
                    log.warning(f"Could not create linestring for path between nodes {a}-{b}, using straight line")
                    # Create straight line as fallback
                    try:
                        a_coords = (self.g.nodes[a]['x'], self.g.nodes[a]['y'])
                        b_coords = (self.g.nodes[b]['x'], self.g.nodes[b]['y'])
                        linestring = shapely.geometry.LineString([a_coords, b_coords])
                        straight_line_edges += 1
                    except (KeyError, AttributeError) as e:
                        log.error(f"Cannot create straight line for edge {a}-{b}: {str(e)}")
                        continue
                
                # Create edge data with the path geometry
                data = {
                    'length': length,
                    'augmented': True,
                    'path': path,
                    'geometry': linestring,
                    'from': a,
                    'to': b,
                }
                
                # Add the edge to the augmented graph
                self.g_augmented.add_edge(a, b, **data)
                total_edges_added += 1
                
                if straight_line_edges > 0:
                    log.warning(f"Created straight line for edge {a}-{b} - STRAIGHT LINE WILL BE VISIBLE IN ROUTE")
                else:
                    log.info(f"Added augmented edge {a}-{b} with real road geometry")
                    
            except nx.NetworkXNoPath:
                log.error(f"No path found between nodes {a}-{b}")
                continue
            except Exception as e:
                log.error(f"Error creating augmented edge {a}-{b}: {str(e)}")
                continue
        
        # Log summary of edge additions
        if total_edges_added > 0:
            log.warning(f"Augmented edge summary:")
            log.warning(f"  - Total edges added: {total_edges_added}")
            log.warning(f"  - Edges using straight lines: {straight_line_edges}")
            log.warning(f"  - Edges using real geometry: {total_edges_added - straight_line_edges}")
            if straight_line_edges > 0:
                log.warning(f"  - {straight_line_edges} edges ({(straight_line_edges/total_edges_added)*100:.1f}%) are using straight lines!")
        
        log.info('post augmentation eulerian=%s', nx.is_eulerian(self.g_augmented))
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
        log.info('converting directed graph to undirected')

        # Store the original directed graph before any modifications
        self.g_directed = self.g.copy()
        
        # Convert to undirected for processing
        self.g = self.g_directed.to_undirected()
        
        # Ensure coordinates are preserved in the undirected graph
        for node in self.g.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g.nodes[node]['x'] = x
                self.g.nodes[node]['y'] = y
        
        # Find connected components
        components = list(nx.connected_components(self.g))
        if len(components) > 1:
            log.warning(f'Graph has {len(components)} disconnected components')
            # Log the size of each component
            for i, comp in enumerate(components):
                log.info(f'Component {i}: {len(comp)} nodes')
            
            # Find edges needed to connect components
            connecting_edges = self.find_connecting_edges(components)
            
            # Add the connecting edges to our graph
            for u, v, data in connecting_edges:
                # Add edge in both directions since this is an undirected graph
                self.g.add_edge(u, v, **data)
                # Add reverse edge with same data
                reverse_data = dict(data)
                if 'geometry' in reverse_data:
                    # Reverse the geometry for the opposite direction
                    reverse_data['geometry'] = shapely.geometry.LineString(list(reverse_data['geometry'].coords)[::-1])
                self.g.add_edge(v, u, **reverse_data)
                log.debug(f'Added connecting edge {u}-{v} with geometry')
            
            # Verify the graph is now connected
            if not nx.is_connected(self.g):
                log.error("Graph is still disconnected after adding connecting edges")
            else:
                log.info(f"Successfully connected graph by adding {len(connecting_edges)} edges")
        
        self.print_edges(self.g)
        
        # Create augmented graph and ensure coordinates are preserved
        self.g_augmented = self.g.copy()
        for node in self.g_augmented.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g_augmented.nodes[node]['x'] = x
                self.g_augmented.nodes[node]['y'] = y
        
        self.odd_nodes = self.find_odd_nodes()
        return


    ##
    ##
    def optimise_dead_ends(self):

        # preempt virtual path augmentation for the case of a dead-end
        # road.  Here the optimum combination pair is its only
        # neighbour node, so why bother iterating through all the
        # pairs to find that.

        # XXX - not quite clean yet.. we are augmenting the original
        # grpah.. need a cleaner way to pass changes through the
        # processing pipeline.

        deadends = { i for i, n in self.g.degree if n == 1 }

        n1 = len(self.find_odd_nodes())

        for deadend in deadends:

            neighbours = self.g[deadend]

            #node_data = self.g.nodes[deadend]
            #log.info('deadend_ndoe=%s, data=%s', deadend, node_data)
            log.debug('deadend_node=%s', deadend)

            if len(neighbours) != 1:
                log.error('wrong number of neighbours for a dead-end street')
                continue

            for neighbour in neighbours.keys():
                log.debug('neighbour=%s', neighbour)

                edge_data = dict(self.g.get_edge_data(deadend, neighbour, 0))
                edge_data['augmented'] = True
                
                log.debug('  creating new edge (%s,%s) - data=%s', deadend, neighbour, edge_data)

                self.g.add_edge(deadend, neighbour, **edge_data)

                pass

            pass

        # fix up the stuff we just busted.  XXX - this should not be
        # hidden in here.

        self.odd_nodes = self.find_odd_nodes()
        self.g_augmented = self.g.copy()

        n2 = len(self.odd_nodes)

        log.info('odd_nodes_before=%d, odd_nodes_after=%d', n1, n2)
        log.info('optimised %d nodes out', n1 - n2)

        return

    ##
    ##
    def determine_combinations(self):

        log.info('eulerian=%s, odd_nodes=%s', nx.is_eulerian(self.g), len(self.odd_nodes))

        odd_node_pairs = self.get_pair_combinations(self.odd_nodes)

        log.info('combinations=%s', len(odd_node_pairs))

        odd_pair_paths = self.get_shortest_path_pairs(self.g, odd_node_pairs)

        # XXX - this part doesn't work well because it doesn't
        # consider the direction of the paths.

        # create a temporary graph of odd pairs.. really we should be
        # doing the combination max calculations here.

        self.g_odd_nodes = nx.Graph()

        for k, length in odd_pair_paths.items():
            i,j = k
            attrs = {
                'length': length,
                'weight': -length,
            }

            self.g_odd_nodes.add_edge(i, j, **attrs)
            pass

        log.info('new_nodes=%s, edges=%s, eulerian=%s', self.g_odd_nodes.order(), self.g_odd_nodes.size(), nx.is_eulerian(self.g_odd_nodes))

        log.info('calculating max weight matching - this can also take a while')

        return

    ##
    ##
    def determine_circuit(self):
        odd_matching = nx.algorithms.max_weight_matching(self.g_odd_nodes, True)
        log.info('augment original graph with %s pairs', len(odd_matching))
        
        self.augment_graph(odd_matching)
        
        # Ensure coordinates are preserved in augmented graph after augmentation
        for node in self.g_augmented.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g_augmented.nodes[node]['x'] = x
                self.g_augmented.nodes[node]['y'] = y
        
        start_node = self.get_start_node(self.g, self.start)
        self.euler_circuit = list(nx.eulerian_circuit(self.g_augmented, source=start_node))
        return

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
            
            # Try to get edge data and geometry
            edge_data = g.get_edge_data(u, v, 0)
            if edge_data is None:
                log.warning(f"No edge data found for {u}-{v}, checking reverse direction")
                edge_data = g.get_edge_data(v, u, 0)
            
            if edge_data and 'geometry' in edge_data and edge_data['geometry'] is not None:
                try:
                    # Get the geometry coordinates
                    geom = edge_data['geometry']
                    coords = list(geom.coords)
                    
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
                    
                    # Add all points except the first if this isn't the first segment
                    # (to avoid duplicating points)
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
            
        # Log summary of geometry usage
        if straight_line_segments > 0:
            log.warning(f"Path geometry summary:")
            log.warning(f"  - Total segments: {total_segments}")
            log.warning(f"  - Segments using straight lines: {straight_line_segments}")
            log.warning(f"  - Segments using real geometry: {total_segments - straight_line_segments}")
            log.warning(f"  - {straight_line_segments} segments ({(straight_line_segments/total_segments)*100:.1f}%) are using straight lines!")
        
        try:
            return shapely.geometry.LineString(all_coords)
        except (ValueError, TypeError) as e:
            log.error(f"Failed to create LineString from coordinates: {str(e)}")
            return None

    def _add_straight_line_segment(self, g, u, v, coords_list, segment_index):
        """Helper method to add a straight line segment between two nodes."""
        try:
            u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
            v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
            
            # Only add the first point if this is the first segment or coords_list is empty
            if segment_index == 0 or not coords_list:
                coords_list.append(u_coords)
            coords_list.append(v_coords)
            
            log.warning(f"Added straight line segment for {u}-{v} - STRAIGHT LINE WILL BE VISIBLE IN ROUTE")
        except (KeyError, AttributeError) as e:
            log.error(f"Cannot create straight line segment for {u}-{v}: {str(e)}")

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
        log.info('fetching OSM data bounded by polygon')
        self.g = osmnx.graph_from_polygon(self.region, network_type='bike', simplify=False, custom_filter=self.custom_filter, retain_all=True)
        
        # Store original graph immediately after creation
        self.g_original = self.g.copy()
        
        # Get nodes and edges as GeoDataFrames with explicit geometry
        nodes, edges = osmnx.utils_graph.graph_to_gdfs(self.g, nodes=True, edges=True, node_geometry=True, fill_edge_geometry=True)
        
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
            
            # Keep track of edges to remove
            edges_to_remove = []
            
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
                        
                        # If more than 30% completed, mark for removal
                        if overlap_ratio > 0.3:
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
                if self.g.has_edge(v, u):  # Remove reverse edge if it exists
                    self.g.remove_edge(v, u)
                    edges_actually_removed += 1
            
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
        # create GPX XML.

        # Ensure coordinates are present in the graph
        for node in g.nodes():
            if node in self.node_coords and ('x' not in g.nodes[node] or 'y' not in g.nodes[node]):
                x, y = self.node_coords[node]
                g.nodes[node]['x'] = x
                g.nodes[node]['y'] = y
        
        stats_distance = 0.0
        stats_backtrack = 0.0
        stats_deadends = 0

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'burb {self.name}'
        gpx.author_name = 'optiburb'
        gpx.creator = 'experimental burbing'
        gpx.description = f'experimental burbing route for {self.name}'

        track = gpxpy.gpx.GPXTrack()
        track.name = f'burb trk {self.name}'
        gpx.tracks.append(track)

        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)

        i = 1

        for n, edge in enumerate(edges):
            u, v = edge
            edge_data = g.get_edge_data(*edge, 0)

            log.debug('EDGE [%d] - edge=%s, data=%s', n, edge, edge_data)

            if edge_data is None:
                log.warning('null data for edge %s', edge)
                # Create straight line between nodes
                try:
                    u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                    v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                    segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=u_coords[1], longitude=u_coords[0]))
                    segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=v_coords[1], longitude=v_coords[0]))
                    i += 2
                except (KeyError, AttributeError) as e:
                    log.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                continue

            linestring = edge_data.get('geometry')
            augmented = edge_data.get('augmented')
            stats_distance += edge_data.get('length', 0)

            log.debug(' leg [%d] -> %s (%s,%s,%s,%s,%s)', n, edge_data.get('name', ''), edge_data.get('highway', ''), edge_data.get('surface', ''), edge_data.get('oneway', ''), edge_data.get('access', ''), edge_data.get('length', 0))

            coords_to_use = None
            if linestring:
                directional_linestring = self.directional_linestring(edge, linestring)
                if directional_linestring:
                    coords_to_use = directional_linestring

            if coords_to_use is None:
                # If no valid linestring, create straight line between nodes
                try:
                    u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                    v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                    coords_to_use = [u_coords, v_coords]
                    log.debug(f"Using straight line for edge {edge}")
                except (KeyError, AttributeError) as e:
                    log.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                    continue

            for lon, lat in coords_to_use:
                segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=lat, longitude=lon))
                log.debug('     INDEX[%d] = (%s, %s)', i, lat, lon)
                i += 1

            if edge_data.get('augmented', False):
                stats_backtrack += edge_data.get('length', 0)

        log.info('total distance = %.2fkm', stats_distance/1000.0)
        log.info('backtrack distance = %.2fkm', stats_backtrack/1000.0)
        
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

