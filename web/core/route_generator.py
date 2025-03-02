"""
Route Generator Module

This module handles route generation and GPX file creation for the OptiburB system.
It includes functionality for finding Eulerian circuits and creating GPX tracks
with direction indicators.
"""

import datetime
import gpxpy
import gpxpy.gpx
import networkx as nx
from web.utils.logging import logger
import os
from web.config import settings
import shapely
import requests
import time

class RouteGenerator:
    def __init__(self, geometry_manager):
        """
        Initialize the RouteGenerator.
        
        Args:
            geometry_manager (GeometryManager): Instance of GeometryManager for geometry operations
        """
        self.geometry = geometry_manager
        self.euler_circuit = None

    def _get_elevation(self, lat, lon):
        """
        Get elevation data for a point using the Open-Elevation API.
        
        Args:
            lat (float): Latitude
            lon (float): Longitude
            
        Returns:
            float: Elevation in meters, or None if the request fails
        """
        try:
            # Use Open-Elevation API
            url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"
            response = requests.get(url)
            if response.ok:
                data = response.json()
                if 'results' in data and len(data['results']) > 0:
                    return data['results'][0]['elevation']
            logger.warning(f"Failed to get elevation data for point ({lat}, {lon})")
            return None
        except Exception as e:
            logger.error(f"Error getting elevation data: {str(e)}")
            return None

    def _get_elevations_batch(self, points):
        """
        Get elevation data for multiple points in a single request.
        
        Args:
            points (list): List of (lat, lon) tuples
            
        Returns:
            list: List of elevation values in meters
        """
        try:
            # Format points for the API request
            locations = "|".join(f"{lat},{lon}" for lat, lon in points)
            url = "https://api.open-elevation.com/api/v1/lookup"
            response = requests.post(url, json={
                "locations": [
                    {"latitude": lat, "longitude": lon}
                    for lat, lon in points
                ]
            })
            
            if response.ok:
                data = response.json()
                if 'results' in data:
                    return [result['elevation'] for result in data['results']]
            
            logger.warning(f"Failed to get batch elevation data for {len(points)} points")
            return [None] * len(points)
        except Exception as e:
            logger.error(f"Error getting batch elevation data: {str(e)}")
            return [None] * len(points)

    def filter_completed_roads(self, graph, completed_buffer):
        """Filter out completed roads from the graph."""
        logger.info("=== Starting completed roads filtering ===")
        total_edges = graph.number_of_edges()
        edges_with_geometry = 0
        edges_intersecting = 0
        high_overlap_edges = 0
        uncompleted_edges = set()
        completed_pairs = {}
        nodes_with_uncompleted = set()
        
        # Helper function to safely copy edge data
        def copy_edge_data(data):
            """Helper function to safely copy edge data while preserving types."""
            edge_data = {}
            for key, value in data.items():
                if isinstance(key, str):
                    if key == 'geometry' and value is not None:
                        edge_data[key] = value
                    elif key == 'length' and value is not None:
                        edge_data[key] = float(value)
                    elif key == 'is_straight_line':
                        edge_data[key] = bool(value)  # Preserve boolean value
                    else:
                        edge_data[key] = str(value) if value is not None else ''
            return edge_data
        
        # First identify all edges and their completion status
        for u, v, data in graph.edges(data=True):
            if 'geometry' in data:
                edges_with_geometry += 1
                edge_geom = data['geometry']
                data['is_straight_line'] = False  # Mark as NOT straight line since it has real geometry
            else:
                # Create straight line geometry if none exists
                try:
                    u_coords = (graph.nodes[u]['x'], graph.nodes[u]['y'])
                    v_coords = (graph.nodes[v]['x'], graph.nodes[v]['y'])
                    edge_geom = shapely.geometry.LineString([u_coords, v_coords])
                    data['geometry'] = edge_geom
                    data['is_straight_line'] = True  # Mark as straight line
                except (KeyError, AttributeError) as e:
                    logger.error(f"Cannot create geometry for edge {u}->{v}: {str(e)}")
                    continue
            
            # Always preserve straight line segments
            if data.get('is_straight_line', False):
                uncompleted_edges.add((u, v))
                nodes_with_uncompleted.add(u)
                nodes_with_uncompleted.add(v)
                continue
            
            # Create buffer around edge (5 meters)
            edge_buffer = edge_geom.buffer(0.00005)
            if not edge_buffer.is_valid:
                edge_buffer = edge_buffer.buffer(0)
            
            if edge_buffer.intersects(completed_buffer):
                edges_intersecting += 1
                intersection = edge_buffer.intersection(completed_buffer)
                if not intersection.is_valid:
                    intersection = intersection.buffer(0)
                
                # Calculate overlap ratio based on area
                intersection_area = intersection.area if hasattr(intersection, 'area') else 0
                edge_area = edge_buffer.area if edge_buffer.area > 0 else 1e-10
                overlap_ratio = intersection_area / edge_area
                
                # Lower the overlap threshold to 50% to be more aggressive about marking roads as completed
                if overlap_ratio > 0.5:  # Changed from 0.7 to 0.5
                    high_overlap_edges += 1
                    edge_pair = frozenset([u, v])
                    
                    # If we have a reverse edge, check if it's also completed
                    if graph.has_edge(v, u):
                        rev_data = graph.get_edge_data(v, u)
                        rev_geom = rev_data.get('geometry', edge_geom)
                        rev_buffer = rev_geom.buffer(0.00005)
                        rev_intersection = rev_buffer.intersection(completed_buffer)
                        rev_overlap = rev_intersection.area / rev_buffer.area if rev_buffer.area > 0 else 0
                        
                        if rev_overlap > 0.5:  # Changed from 0.7 to 0.5
                            # Both directions are completed
                            completed_pairs[edge_pair] = ((u, v), (v, u))
                        else:
                            # Only one direction completed, keep the uncompleted direction
                            uncompleted_edges.add((v, u))
                            nodes_with_uncompleted.add(u)
                            nodes_with_uncompleted.add(v)
                    else:
                        # Single direction completed edge
                        completed_pairs[edge_pair] = ((u, v), None)
                else:
                    # Edge is not completed, add it to uncompleted edges
                    uncompleted_edges.add((u, v))
                    nodes_with_uncompleted.add(u)
                    nodes_with_uncompleted.add(v)
                
                # If there's a reverse edge, check if it's completed
                if graph.has_edge(v, u):
                    rev_data = graph.get_edge_data(v, u)
                    rev_geom = rev_data.get('geometry', edge_geom)
                    rev_buffer = rev_geom.buffer(0.00005)
                    if not rev_buffer.intersects(completed_buffer):
                        # Reverse edge is also not completed
                        uncompleted_edges.add((v, u))
                    else:
                        # Check if reverse edge is completed
                        rev_intersection = rev_buffer.intersection(completed_buffer)
                        rev_overlap = rev_intersection.area / rev_buffer.area if rev_buffer.area > 0 else 0
                        if rev_overlap <= 0.5:  # Changed from 0.7 to 0.5
                            uncompleted_edges.add((v, u))
            else:
                # Edge doesn't intersect with completed area at all
                uncompleted_edges.add((u, v))
                nodes_with_uncompleted.add(u)
                nodes_with_uncompleted.add(v)
                
                # If there's a reverse edge, it's also not completed
                if graph.has_edge(v, u):
                    uncompleted_edges.add((v, u))
                    nodes_with_uncompleted.add(u)
                    nodes_with_uncompleted.add(v)

        logger.info(f"Edge analysis:")
        logger.info(f"  - Total edges: {total_edges}")
        logger.info(f"  - Edges with geometry: {edges_with_geometry}")
        logger.info(f"  - Edges intersecting completed area: {edges_intersecting}")
        logger.info(f"  - Edges with high overlap (>50%): {high_overlap_edges}")
        logger.info(f"  - Completed edge pairs: {len(completed_pairs)}")
        logger.info(f"  - Uncompleted edges to preserve: {len(uncompleted_edges)}")
        logger.info(f"  - Nodes with uncompleted edges: {len(nodes_with_uncompleted)}")
        
        # Create a subgraph of uncompleted edges to find connected regions
        uncompleted_graph = nx.DiGraph()
        for u, v in uncompleted_edges:
            edge_data = copy_edge_data(graph.get_edge_data(u, v))
            uncompleted_graph.add_edge(u, v, **edge_data)
        
        # Find weakly connected components (regions of uncompleted edges)
        uncompleted_components = list(nx.weakly_connected_components(uncompleted_graph))
        logger.info(f"Found {len(uncompleted_components)} regions of uncompleted edges")
        
        # Sort components by size (number of nodes) in descending order
        uncompleted_components.sort(key=len, reverse=True)
        
        # Start with the largest component
        largest_component = uncompleted_components[0]
        filtered_graph = uncompleted_graph.subgraph(largest_component).copy()
        logger.info(f"Starting with largest component containing {len(largest_component)} nodes")
        
        # Try to connect other components to the largest one
        for i in range(1, len(uncompleted_components)):
            component = uncompleted_components[i]
            component_size = len(component)
            logger.info(f"Attempting to connect component {i} with {component_size} nodes")
            
            # Find best connection between current component and filtered graph
            best_path = None
            min_completed_edges = float('inf')
            best_source = None
            best_target = None
            best_reverse_path = None
            
            for source in component:
                for target in filtered_graph.nodes():
                    if source != target:
                        try:
                            # Try to find a path through the original graph
                            forward_path = nx.shortest_path(graph, source, target, weight='length')
                            forward_completed = sum(1 for j in range(len(forward_path)-1) 
                                               if frozenset([forward_path[j], forward_path[j+1]]) in completed_pairs)
                            
                            # Try to find a reverse path to maintain balance
                            try:
                                reverse_path = nx.shortest_path(graph, target, source, weight='length')
                                reverse_completed = sum(1 for j in range(len(reverse_path)-1)
                                                   if frozenset([reverse_path[j], reverse_path[j+1]]) in completed_pairs)
                                
                                # Only consider this pair if we can find both forward and reverse paths
                                total_completed = forward_completed + reverse_completed
                                if total_completed < min_completed_edges:
                                    min_completed_edges = total_completed
                                    best_path = forward_path
                                    best_reverse_path = reverse_path
                                    best_source = source
                                    best_target = target
                            except nx.NetworkXNoPath:
                                continue
                        except nx.NetworkXNoPath:
                            continue
            
            if best_path and best_reverse_path:
                # Add both forward and reverse paths to maintain balance
                for path in [best_path, best_reverse_path]:
                    for j in range(len(path)-1):
                        u, v = path[j], path[j+1]
                        if not filtered_graph.has_edge(u, v):
                            edge_data = copy_edge_data(graph.get_edge_data(u, v))
                            filtered_graph.add_edge(u, v, **edge_data)
                
                # Add all edges from the component
                component_edges = list(uncompleted_graph.subgraph(component).edges())
                for u, v in component_edges:
                    if not filtered_graph.has_edge(u, v):
                        edge_data = copy_edge_data(uncompleted_graph.get_edge_data(u, v))
                        filtered_graph.add_edge(u, v, **edge_data)
                    # Add reverse edge if it exists in the original graph
                    if graph.has_edge(v, u) and not filtered_graph.has_edge(v, u):
                        rev_data = copy_edge_data(graph.get_edge_data(v, u))
                        filtered_graph.add_edge(v, u, **rev_data)
                
                logger.info(f"Connected component {i} using balanced paths between {best_source} and {best_target}")
            else:
                logger.warning(f"Could not find balanced connection for component {i}")
                # Try to add the component as a separate circuit
                component_graph = uncompleted_graph.subgraph(component).copy()
                if nx.is_weakly_connected(component_graph):
                    # Balance the component internally
                    for u, v in component_graph.edges():
                        if not filtered_graph.has_edge(u, v):
                            edge_data = copy_edge_data(uncompleted_graph.get_edge_data(u, v))
                            filtered_graph.add_edge(u, v, **edge_data)
                        # Add reverse edge if needed for balance
                        if not filtered_graph.has_edge(v, u):
                            if graph.has_edge(v, u):
                                rev_data = copy_edge_data(graph.get_edge_data(v, u))
                            else:
                                rev_data = copy_edge_data(uncompleted_graph.get_edge_data(u, v))
                            filtered_graph.add_edge(v, u, **rev_data)
                    logger.info(f"Added component {i} as separate balanced circuit")
        
        # Balance the filtered graph
        unbalanced_nodes = []
        for node in filtered_graph.nodes():
            in_degree = filtered_graph.in_degree(node)
            out_degree = filtered_graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            logger.info(f"Attempting to balance {len(unbalanced_nodes)} nodes")
            needs_in = [(node, out_deg - in_deg) for node, in_deg, out_deg in unbalanced_nodes if in_deg < out_deg]
            needs_out = [(node, in_deg - out_deg) for node, in_deg, out_deg in unbalanced_nodes if in_deg > out_deg]
            
            # Try to balance nodes using shortest paths and their reverses
            for (target, needed) in needs_in:
                for (source, available) in needs_out:
                    if needed > 0 and available > 0:
                        try:
                            # Find forward path
                            forward_path = nx.shortest_path(graph, source, target, weight='length')
                            # Find reverse path
                            reverse_path = nx.shortest_path(graph, target, source, weight='length')
                            
                            # Add both paths to maintain balance
                            for path in [forward_path, reverse_path]:
                                for i in range(len(path)-1):
                                    u, v = path[i], path[i+1]
                                    if not filtered_graph.has_edge(u, v):
                                        edge_data = copy_edge_data(graph.get_edge_data(u, v))
                                        filtered_graph.add_edge(u, v, **edge_data)
                            
                            needed -= 1
                            available -= 1
                            if needed == 0 or available == 0:
                                break
                        except nx.NetworkXNoPath:
                            continue
        
        # Final check for balance
        still_unbalanced = []
        for node in filtered_graph.nodes():
            in_deg = filtered_graph.in_degree(node)
            out_deg = filtered_graph.out_degree(node)
            if in_deg != out_deg:
                still_unbalanced.append((node, in_deg, out_deg))
        
        if still_unbalanced:
            logger.warning(f"{len(still_unbalanced)} nodes remain unbalanced")
            # Add reverse edges for all edges to force balance
            edges_to_add = []
            for u, v in filtered_graph.edges():
                if not filtered_graph.has_edge(v, u):
                    edges_to_add.append((v, u))
            
            for v, u in edges_to_add:
                if graph.has_edge(v, u):
                    edge_data = copy_edge_data(graph.get_edge_data(v, u))
                else:
                    # Create reverse edge data from forward edge
                    edge_data = copy_edge_data(filtered_graph.get_edge_data(u, v))
                    if 'geometry' in edge_data:
                        # Reverse the geometry
                        coords = list(edge_data['geometry'].coords)
                        edge_data['geometry'] = shapely.geometry.LineString(coords[::-1])
                filtered_graph.add_edge(v, u, **edge_data)
            
            logger.info("Added reverse edges to force graph balance")
        
        # Verify the graph is weakly connected
        if not nx.is_weakly_connected(filtered_graph):
            components = list(nx.weakly_connected_components(filtered_graph))
            logger.warning(f"Final graph has {len(components)} disconnected components")
            # Use the largest component
            largest = max(components, key=len)
            filtered_graph = filtered_graph.subgraph(largest).copy()
            # Ensure the largest component is balanced by adding reverse edges
            edges_to_add = []
            for u, v in filtered_graph.edges():
                if not filtered_graph.has_edge(v, u):
                    edges_to_add.append((v, u))
            
            for v, u in edges_to_add:
                if graph.has_edge(v, u):
                    edge_data = copy_edge_data(graph.get_edge_data(v, u))
                else:
                    edge_data = copy_edge_data(filtered_graph.get_edge_data(u, v))
                    if 'geometry' in edge_data:
                        coords = list(edge_data['geometry'].coords)
                        edge_data['geometry'] = shapely.geometry.LineString(coords[::-1])
                filtered_graph.add_edge(v, u, **edge_data)
            
            logger.info(f"Using balanced largest component with {len(largest)} nodes")
        
        edges_removed = total_edges - filtered_graph.number_of_edges()
        logger.info(f"Successfully removed {edges_removed} completed road segments")
        logger.info(f"Final graph has {filtered_graph.number_of_edges()} edges")
        logger.info("=== Completed roads filtering finished ===")
        
        return filtered_graph

    def determine_circuit(self, graph, start_node=None, completed_area=None):
        """
        Determine the Eulerian circuit in the directed graph.
        
        Args:
            graph (nx.DiGraph): The graph to find the circuit in
            start_node: Optional starting node
            completed_area: Optional shapely.geometry.Polygon of completed roads to exclude
            
        Returns:
            list: The Eulerian circuit as a list of node pairs with edge data
        """
        logger.info('Starting to find Eulerian circuit in directed graph')
        
        original_start = start_node  # Store the original start node
        
        # Filter out completed roads if requested
        if completed_area is not None:
            graph = self.filter_completed_roads(graph, completed_area)
            logger.info(f"Working with filtered graph containing {graph.number_of_edges()} edges")
        
        # Count initial straight lines and store edge data
        initial_straight_lines = 0
        edge_data_map = {}  # Store edge data keyed by (u,v)
        for u, v, data in graph.edges(data=True):
            edge_data_map[(u,v)] = data.copy()  # Make a copy to preserve all attributes
            if data.get('is_straight_line', False):
                initial_straight_lines += 1
                logger.debug(f"Found straight line edge {u}->{v} in input graph")
        
        logger.info(f"Input graph has {initial_straight_lines} straight line edges")
        
        # Verify the graph has nodes
        if graph.number_of_nodes() == 0:
            raise ValueError("Graph has no nodes")
            
        # Handle start node selection
        if start_node is not None and start_node not in graph.nodes():
            logger.warning(f"Original start node {start_node} not found in filtered graph")
            logger.info(f"Available nodes: {list(graph.nodes())[:10]}...")
            
            # Try to find the closest available node to the original start
            if hasattr(graph, 'nodes') and all('x' in graph.nodes[n] and 'y' in graph.nodes[n] for n in graph.nodes()):
                # Get coordinates of all nodes
                closest_node = None
                min_distance = float('inf')
                start_coords = None
                
                # Try to get coordinates of original start node from the graph attributes
                for node in graph.nodes():
                    if hasattr(graph.nodes[node], 'original_id') and graph.nodes[node]['original_id'] == original_start:
                        start_coords = (graph.nodes[node]['x'], graph.nodes[node]['y'])
                        break
                
                if start_coords:
                    # Find closest node by Euclidean distance
                    for node in graph.nodes():
                        node_coords = (graph.nodes[node]['x'], graph.nodes[node]['y'])
                        dist = ((node_coords[0] - start_coords[0])**2 + (node_coords[1] - start_coords[1])**2)**0.5
                        if dist < min_distance:
                            min_distance = dist
                            closest_node = node
                    
                    if closest_node is not None:
                        start_node = closest_node
                        logger.info(f"Using closest available node {start_node} as start point")
                    else:
                        start_node = list(graph.nodes())[0]
                        logger.info(f"Using first available node {start_node} as start point")
                else:
                    start_node = list(graph.nodes())[0]
                    logger.info(f"Using first available node {start_node} as start point")
            else:
                start_node = list(graph.nodes())[0]
                logger.info(f"Using first available node {start_node} as start point")
        elif start_node is None:
            start_node = list(graph.nodes())[0]
            logger.info(f"Using node {start_node} as start point")
        
        # First verify the graph is balanced
        unbalanced_nodes = []
        for node in graph.nodes():
            in_degree = graph.in_degree(node)
            out_degree = graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
                logger.error(f"Node {node} has imbalanced degrees: in={in_degree}, out={out_degree}")
        
        if unbalanced_nodes:
            raise ValueError(f"Graph is not balanced. Found {len(unbalanced_nodes)} unbalanced nodes.")
        
        # Verify graph is weakly connected
        if not nx.is_weakly_connected(graph):
            components = list(nx.weakly_connected_components(graph))
            raise ValueError(f"Graph is not connected. Found {len(components)} weakly connected components.")
        
        # Log graph state before finding circuit
        logger.info(f"Graph state before finding circuit:")
        logger.info(f"  - Number of nodes: {graph.number_of_nodes()}")
        logger.info(f"  - Number of edges: {graph.number_of_edges()}")
        logger.info(f"  - Start node: {start_node}")
        logger.info(f"  - Start node in-degree: {graph.in_degree(start_node)}")
        logger.info(f"  - Start node out-degree: {graph.out_degree(start_node)}")
        
        # Find Eulerian circuit
        try:
            # For directed graphs, we use nx.eulerian_circuit directly
            circuit = list(nx.eulerian_circuit(graph, source=start_node))
            logger.info(f"Found initial Eulerian circuit with {len(circuit)} edges")
            
            # Create a new list that includes edge data
            self.euler_circuit = []
            straight_lines = 0
            for u, v in circuit:
                # Get edge data from our stored map
                edge_data = edge_data_map.get((u,v))
                if edge_data is None:
                    logger.warning(f"No edge data found for edge {(u,v)}")
                    edge_data = {}
                else:
                    # Verify straight line flag is preserved
                    if edge_data.get('is_straight_line', False):
                        straight_lines += 1
                        logger.debug(f"Found straight line edge {u}->{v} in circuit")
                
                self.euler_circuit.append((u, v, edge_data))
            
            logger.info(f"Circuit contains {straight_lines} straight line edges")
            
            # Verify all edges are included
            circuit_edges = set((u,v) for u,v,_ in self.euler_circuit)
            all_edges = set(graph.edges())
            missing_edges = all_edges - circuit_edges
            
            if missing_edges:
                logger.error(f"Circuit is incomplete. Missing {len(missing_edges)} edges:")
                for edge in missing_edges:
                    logger.error(f"Missing edge: {edge}")
                raise ValueError(f"Circuit is incomplete. Missing {len(missing_edges)} edges.")
            
            # Verify straight line count matches
            if straight_lines != initial_straight_lines:
                logger.warning(f"Straight line count mismatch: {straight_lines} in circuit vs {initial_straight_lines} in input graph")
                # Log details of straight line edges for debugging
                logger.debug("Straight line edges in input graph:")
                for (u,v), data in edge_data_map.items():
                    if data.get('is_straight_line', False):
                        logger.debug(f"  {u}->{v}")
                logger.debug("Straight line edges in circuit:")
                for u, v, data in self.euler_circuit:
                    if data.get('is_straight_line', False):
                        logger.debug(f"  {u}->{v}")
            
            logger.info("Successfully verified circuit includes all edges")
            return self.euler_circuit
            
        except nx.NetworkXError as e:
            logger.error(f"Failed to find Eulerian circuit: {str(e)}")
            raise ValueError(f"Failed to find Eulerian circuit: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error finding Eulerian circuit: {str(e)}")
            raise

    def create_gpx_track(self, graph, edges, simplify=False):
        """
        Create a GPX track with direction indicators and elevation data.
        
        Args:
            graph (nx.DiGraph): The graph containing the edges
            edges (list): List of edges with data (u, v, data) to include in the track
            simplify (bool): Whether to simplify the resulting GPX
            
        Returns:
            str: The filename of the created GPX file
        """
        stats_distance = 0.0
        stats_backtrack = 0.0
        total_direction_markers = 0
        straight_line_edges = 0

        logger.info('Creating GPX track with direction indicators and elevation data')
        logger.info(f'Number of edges to process: {len(edges)}')

        # First count straight line edges in input and store them for verification
        straight_line_set = set()  # Store (u,v) pairs for straight line edges
        initial_straight_lines = 0
        for u, v, data in edges:
            if data and data.get('is_straight_line', False):
                initial_straight_lines += 1
                straight_line_set.add((u,v))
                logger.debug(f"Input edge {u}->{v} is marked as straight line")
        logger.info(f"Input graph has {initial_straight_lines} straight line edges")

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'optiburb_route'
        gpx.author_name = 'optiburb'
        gpx.creator = 'OptiburB Route Generator'
        gpx.description = 'Generated route with direction indicators'
        gpx.keywords = 'directed route,one-way streets'

        # Create two tracks: one for real roads and one for straight lines
        real_track = gpxpy.gpx.GPXTrack()
        real_track.name = 'optiburb_real_roads'
        real_track.type = 'directed'
        gpx.tracks.append(real_track)

        straight_track = gpxpy.gpx.GPXTrack()
        straight_track.name = 'optiburb_straight_lines'
        straight_track.type = 'balancing'
        gpx.tracks.append(straight_track)

        real_segment = gpxpy.gpx.GPXTrackSegment()
        real_track.segments.append(real_segment)

        straight_segment = gpxpy.gpx.GPXTrackSegment()
        straight_track.segments.append(straight_segment)

        i = 1
        arrow_interval = 3  # Add direction arrow every 3 points

        # Collect all coordinates first for batch elevation request
        all_coords = []
        coord_indices = {}  # Map (lat, lon) to index in all_coords
        points_to_create = []  # Store point creation info for later

        # Function to sample points from a line
        def sample_points(coords, max_points=10):
            if len(coords) <= max_points:
                return coords
            # Always include first and last points
            if len(coords) == 2:
                return coords
            # For longer segments, sample evenly
            step = (len(coords) - 1) / (max_points - 1)
            indices = [int(i * step) for i in range(max_points - 1)] + [len(coords) - 1]
            return [coords[i] for i in indices]

        for n, (u, v, edge_data) in enumerate(edges):
            # First check if this edge was originally marked as straight line
            original_is_straight = (u,v) in straight_line_set
            
            if edge_data is None:
                logger.warning('null data for edge %s', (u,v))
                try:
                    u_coords = (graph.nodes[u]['x'], graph.nodes[u]['y'])
                    v_coords = (graph.nodes[v]['x'], graph.nodes[v]['y'])
                    edge_data = {
                        'geometry': shapely.geometry.LineString([u_coords, v_coords]), 
                        'is_straight_line': True,
                        'length': self.geometry.calculate_distance(u_coords, v_coords)
                    }
                    straight_line_edges += 1
                    logger.debug(f"Created straight line for edge {(u,v)}")
                except (KeyError, AttributeError) as e:
                    logger.error(f"Cannot create straight line for edge {(u,v)}: {str(e)}")
                    continue

            # Get edge attributes and explicitly check is_straight_line flag
            is_straight_line = original_is_straight or edge_data.get('is_straight_line', False)
            if is_straight_line:
                straight_line_edges += 1
                logger.debug(f"Processing straight line edge {(u,v)}")

            linestring = edge_data.get('geometry')
            augmented = edge_data.get('augmented')
            
            # Calculate distance for all edges
            if 'length' in edge_data:
                stats_distance += edge_data['length']
                if augmented:
                    stats_backtrack += edge_data['length']

            coords_to_use = None
            if linestring:
                try:
                    # Extract coordinates from LineString
                    if hasattr(linestring, 'coords'):
                        coords_list = list(linestring.coords)
                        if coords_list:
                            # Sample points for long segments
                            coords_to_use = sample_points(coords_list)
                
                    # If we couldn't get coords directly, try directional linestring
                    if not coords_to_use:
                        directional_coords = self.geometry.get_directional_linestring((u,v), linestring, graph.nodes)
                        if directional_coords:
                            coords_to_use = sample_points(directional_coords)
                except Exception as e:
                    logger.error(f"Error extracting coordinates from LineString: {str(e)}")

            if coords_to_use is None:
                try:
                    u_coords = (graph.nodes[u]['x'], graph.nodes[u]['y'])
                    v_coords = (graph.nodes[v]['x'], graph.nodes[v]['y'])
                    coords_to_use = [u_coords, v_coords]  # No need to sample 2-point lines
                    # Only calculate length if needed
                    if 'length' not in edge_data:
                        edge_data['length'] = self.geometry.calculate_distance(u_coords, v_coords)
                except (KeyError, AttributeError) as e:
                    logger.error(f"Cannot create coordinates for edge {(u,v)}: {str(e)}")
                    continue

            if not coords_to_use:
                logger.error(f"No valid coordinates found for edge {(u,v)}")
                continue

            # Store coordinates and point creation info
            target_segment = straight_segment if is_straight_line else real_segment
            for j, coord in enumerate(coords_to_use):
                lat, lon = coord[1], coord[0]
                coord_key = (lat, lon)
                
                # Only add coordinates we haven't seen before
                if coord_key not in coord_indices:
                    coord_indices[coord_key] = len(all_coords)
                    all_coords.append((lat, lon))
                
                # Store point creation info
                points_to_create.append({
                    'coord_key': coord_key,
                    'segment': target_segment,
                    'is_straight_line': is_straight_line,
                    'edge': (u, v),
                    'position': j,
                    'total_positions': len(coords_to_use),
                    'i': i + j
                })

            i += len(coords_to_use)

        # Get elevations in batches
        logger.info(f"Fetching elevation data for {len(all_coords)} unique points")
        batch_size = 500  # Increased batch size
        elevations = {}
        
        for start in range(0, len(all_coords), batch_size):
            end = min(start + batch_size, len(all_coords))
            batch = all_coords[start:end]
            
            try:
                batch_elevations = self._get_elevations_batch(batch)
                if batch_elevations:
                    for (lat, lon), elevation in zip(batch, batch_elevations):
                        if elevation is not None:
                            elevations[(lat, lon)] = elevation
                
                # Add a small delay between batches
                if end < len(all_coords):
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error getting elevation batch {start}-{end}: {str(e)}")

        # Create points with elevation data
        for point_info in points_to_create:
            coord_key = point_info['coord_key']
            lat, lon = coord_key
            
            point = gpxpy.gpx.GPXTrackPoint(lat, lon)
            
            # Add elevation if available
            elevation = elevations.get(coord_key)
            if elevation is not None:
                point.elevation = elevation
            
            # Set point type
            point.type = 'straight_line' if point_info['is_straight_line'] else 'route'
            
            # Add point to appropriate segment
            point_info['segment'].points.append(point)
            
            # Add direction markers for non-straight lines
            if (not point_info['is_straight_line'] and 
                point_info['position'] > 0 and 
                point_info['position'] < point_info['total_positions'] and 
                point_info['i'] % arrow_interval == 0):
                
                marker = gpxpy.gpx.GPXTrackPoint(lat, lon)
                marker.type = 'direction'
                
                # Add elevation to marker
                if elevation is not None:
                    marker.elevation = elevation
                
                # Calculate bearing from previous point
                prev_info = points_to_create[points_to_create.index(point_info) - 1]
                prev_lat, prev_lon = prev_info['coord_key']
                bearing = self.geometry.calculate_bearing(prev_lat, prev_lon, lat, lon)
                marker.comment = str(bearing)
                
                point_info['segment'].points.append(marker)
                total_direction_markers += 1

        # Verify we have points in at least one segment
        if not real_segment.points and not straight_segment.points:
            raise ValueError("No valid points were added to the GPX track")

        # Verify straight line count matches input
        if straight_line_edges != initial_straight_lines:
            logger.warning(f"Straight line count mismatch in GPX: found {straight_line_edges}, expected {initial_straight_lines}")
            logger.debug("Original straight line edges:")
            for u, v in straight_line_set:
                logger.debug(f"  {u}->{v}")

        logger.info('total distance = %.2fkm', stats_distance/1000.0)
        logger.info('backtrack distance = %.2fkm', stats_backtrack/1000.0)
        logger.info(f'Total direction markers added to GPX: {total_direction_markers}')
        logger.info(f'Total straight line edges in GPX: {straight_line_edges}')
        
        if simplify:
            logger.info('simplifying GPX')
            # Store direction markers and point types before simplification
            point_data = []
            for segment in [real_segment, straight_segment]:
                for point in segment.points:
                    if hasattr(point, 'type'):
                        point_data.append({
                            'lat': point.latitude,
                            'lon': point.longitude,
                            'type': point.type,
                            'comment': point.comment if hasattr(point, 'comment') else None,
                            'elevation': point.elevation if hasattr(point, 'elevation') else None
                        })
            
            # Remove direction markers temporarily from real roads segment
            real_segment.points = [p for p in real_segment.points if not (hasattr(p, 'type') and p.type == 'direction')]
            
            # Simplify both tracks
            gpx.simplify()
            
            # Re-add point types and direction markers to real roads
            simplified_points = real_segment.points[:]
            real_segment.points = []
            
            arrow_interval = max(3, len(simplified_points) // (total_direction_markers + 1))
            for i, point in enumerate(simplified_points):
                # Try to find matching original point to preserve type and elevation
                closest_original = min(point_data, key=lambda p: 
                    abs(p['lat'] - point.latitude) + abs(p['lon'] - point.longitude))
                
                point.type = 'route'  # All points in real_segment are routes
                if closest_original.get('elevation') is not None:
                    point.elevation = closest_original['elevation']
                real_segment.points.append(point)
                
                # Add direction markers at intervals
                if i > 0 and i < len(simplified_points) - 1 and i % arrow_interval == 0:
                    marker = gpxpy.gpx.GPXTrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude
                    )
                    marker.type = 'direction'
                    marker.symbol = '➜'
                    if closest_original.get('elevation') is not None:
                        marker.elevation = closest_original['elevation']
                    next_point = simplified_points[i + 1]
                    bearing = self.geometry.calculate_bearing(
                        point.latitude, point.longitude,
                        next_point.latitude, next_point.longitude
                    )
                    marker.comment = str(round(bearing, 1))
                    real_segment.points.append(marker)

            # Mark all points in straight line segment
            for point in straight_segment.points:
                point.type = 'straight_line'

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'optiburb_route_{timestamp}.gpx'
        filepath = os.path.join(settings.UPLOAD_FOLDER, filename)

        logger.info('Saving GPX track to %s', filepath)
        with open(filepath, 'w') as f:
            f.write(gpx.to_xml())

        return filename

    def _add_track_points(self, segment, coords, start_index, arrow_interval):
        """
        Add track points with direction indicators at specified intervals.
        
        Args:
            segment (gpxpy.gpx.GPXTrackSegment): The segment to add points to
            coords (list): List of coordinate pairs
            start_index (int): Starting index for point numbering
            arrow_interval (int): Interval for adding direction indicators
            
        Returns:
            int: Number of direction markers added, or None if there was an error
        """
        if not coords or len(coords) < 2:
            logger.warning(f"Invalid coordinates provided: {coords}")
            return None

        direction_markers_added = 0
        logger.debug(f'Adding track points for segment starting at index {start_index}, interval={arrow_interval}')
        logger.debug(f'Number of coordinates to process: {len(coords)}')
        
        # For very short segments (2 points), always add a direction marker at the first point
        is_short_segment = len(coords) == 2
        
        try:
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
                    bearing = self.geometry.calculate_bearing(lat, lon, next_lat, next_lon)
                    
                    # Set point attributes for direction marker
                    point.type = 'direction'
                    point.symbol = '➜'
                    point.comment = str(round(bearing, 1))
                    
                    direction_markers_added += 1
                    logger.debug(f'Added direction marker at point {start_index + i}: bearing={bearing}°, coords=({lat}, {lon})')
                
                segment.points.append(point)
            
            logger.debug(f'Added {direction_markers_added} direction markers in this segment')
            if direction_markers_added == 0:
                logger.warning('No direction markers were added in this segment')
                logger.warning(f'Segment details: start_index={start_index}, coords={len(coords)}, interval={arrow_interval}')
            
            return direction_markers_added

        except Exception as e:
            logger.error(f"Error adding track points: {str(e)}")
            return None 