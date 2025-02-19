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

class RouteGenerator:
    def __init__(self, geometry_manager):
        """
        Initialize the RouteGenerator.
        
        Args:
            geometry_manager (GeometryManager): Instance of GeometryManager for geometry operations
        """
        self.geometry = geometry_manager
        self.euler_circuit = None

    def filter_completed_roads(self, graph, completed_area):
        """
        Filter out completed roads from the graph while maintaining balance.
        
        Args:
            graph (nx.DiGraph): The graph to filter
            completed_area (shapely.geometry.Polygon): The area of completed roads
            
        Returns:
            nx.DiGraph: The filtered graph
        """
        if not completed_area:
            logger.warning("No completed area provided - skipping filtering")
            return graph

        logger.info("=== Starting completed roads filtering ===")
        
        # Create a buffer around the completed area (15 meters)
        completed_buffer = completed_area.buffer(0.00015)  # ~15 meter buffer
        if completed_buffer.is_empty:
            logger.error("Completed area buffer is empty - no filtering will be done")
            return graph

        # First pass: identify completed and uncompleted edges
        total_edges = graph.number_of_edges()
        edges_with_geometry = 0
        edges_intersecting = 0
        high_overlap_edges = 0
        
        # Track completed and uncompleted edges separately
        completed_pairs = {}  # {frozenset(u,v): (edge1, edge2)} for completed bidirectional edges
        uncompleted_edges = set()
        nodes_with_uncompleted = set()  # Nodes that have uncompleted edges
        
        # Helper function to safely copy edge data
        def copy_edge_data(data):
            edge_data = {}
            for key, value in data.items():
                if isinstance(key, str):
                    if key == 'geometry' and value is not None:
                        edge_data[key] = value
                    elif key == 'length' and value is not None:
                        edge_data[key] = float(value)
                    else:
                        edge_data[key] = str(value) if value is not None else ''
            return edge_data
        
        # First identify all edges and their completion status
        for u, v, data in graph.edges(data=True):
            if 'geometry' in data:
                edges_with_geometry += 1
                edge_geom = data['geometry']
            else:
                # Create straight line geometry if none exists
                try:
                    u_coords = (graph.nodes[u]['x'], graph.nodes[u]['y'])
                    v_coords = (graph.nodes[v]['x'], graph.nodes[v]['y'])
                    edge_geom = shapely.geometry.LineString([u_coords, v_coords])
                except (KeyError, AttributeError) as e:
                    logger.error(f"Cannot create geometry for edge {u}-{v}: {str(e)}")
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
                
                # If more than 70% completed
                if overlap_ratio > 0.7:
                    high_overlap_edges += 1
                    edge_pair = frozenset([u, v])
                    
                    # If we have a reverse edge, check if it's also completed
                    if graph.has_edge(v, u):
                        rev_data = graph.get_edge_data(v, u)
                        rev_geom = rev_data.get('geometry', edge_geom)
                        rev_buffer = rev_geom.buffer(0.00005)
                        rev_intersection = rev_buffer.intersection(completed_buffer)
                        rev_overlap = rev_intersection.area / rev_buffer.area if rev_buffer.area > 0 else 0
                        
                        if rev_overlap > 0.7:
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
                        if rev_overlap <= 0.7:
                            uncompleted_edges.add((v, u))

        logger.info(f"Edge analysis:")
        logger.info(f"  - Total edges: {total_edges}")
        logger.info(f"  - Edges with geometry: {edges_with_geometry}")
        logger.info(f"  - Edges intersecting completed area: {edges_intersecting}")
        logger.info(f"  - Edges with high overlap (>70%): {high_overlap_edges}")
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
        
        # Create filtered graph starting with uncompleted edges
        filtered_graph = nx.DiGraph()
        
        # Add all uncompleted edges first
        for u, v in uncompleted_edges:
            edge_data = copy_edge_data(graph.get_edge_data(u, v))
            filtered_graph.add_edge(u, v, **edge_data)
        
        # For each component, ensure it's internally balanced first
        for component in uncompleted_components:
            component_nodes = list(component)
            for node in component_nodes:
                in_degree = filtered_graph.in_degree(node)
                out_degree = filtered_graph.out_degree(node)
                if in_degree != out_degree:
                    # Try to find balancing edges within the same component
                    for other_node in component_nodes:
                        if node != other_node:
                            if in_degree < out_degree and filtered_graph.has_edge(other_node, node):
                                # Need incoming edge, copy the reverse of existing edge
                                edge_data = copy_edge_data(graph.get_edge_data(other_node, node))
                                if 'geometry' in edge_data:
                                    # Reverse the geometry for the opposite direction
                                    coords = list(edge_data['geometry'].coords)
                                    edge_data['geometry'] = shapely.geometry.LineString(coords[::-1])
                                filtered_graph.add_edge(other_node, node, **edge_data)
                                in_degree += 1
                            elif in_degree > out_degree and filtered_graph.has_edge(node, other_node):
                                # Need outgoing edge, copy existing edge
                                edge_data = copy_edge_data(graph.get_edge_data(node, other_node))
                                filtered_graph.add_edge(node, other_node, **edge_data)
                                out_degree += 1
                            
                            if in_degree == out_degree:
                                break
        
        # Now connect components using uncompleted roads first, then completed roads if necessary
        if len(uncompleted_components) > 1:
            for i in range(len(uncompleted_components)):
                for j in range(i + 1, len(uncompleted_components)):
                    comp1 = uncompleted_components[i]
                    comp2 = uncompleted_components[j]
                    
                    # Try to find a path using only uncompleted roads first
                    path_found = False
                    min_path_length = float('inf')
                    best_path = None
                    best_source = None
                    best_target = None
                    
                    for source in comp1:
                        for target in comp2:
                            try:
                                # Look for paths in the uncompleted graph
                                if nx.has_path(uncompleted_graph, source, target):
                                    path = nx.shortest_path(uncompleted_graph, source, target, weight='length')
                                    path_length = sum(uncompleted_graph[path[k]][path[k+1]].get('length', 1) 
                                                    for k in range(len(path)-1))
                                    
                                    if path_length < min_path_length:
                                        min_path_length = path_length
                                        best_path = path
                                        best_source = source
                                        best_target = target
                                        path_found = True
                            except Exception:
                                continue
                    
                    if path_found and best_path:
                        # Add the uncompleted road path
                        for k in range(len(best_path) - 1):
                            u, v = best_path[k], best_path[k + 1]
                            if not filtered_graph.has_edge(u, v):
                                edge_data = copy_edge_data(uncompleted_graph.get_edge_data(u, v))
                                filtered_graph.add_edge(u, v, **edge_data)
                        logger.info(f"Connected components using uncompleted road path: {best_source}->{best_target}")
                        continue
                    
                    # If no uncompleted path exists, try using completed roads
                    min_path_length = float('inf')
                    best_path = None
                    best_source = None
                    best_target = None
                    
                    for source in comp1:
                        for target in comp2:
                            try:
                                # Try to find a path through completed roads
                                paths = list(nx.all_simple_paths(graph, source, target, cutoff=5))
                                for path in paths:
                                    path_length = 0
                                    path_valid = True
                                    completed_road_count = 0
                                    
                                    for k in range(len(path) - 1):
                                        u, v = path[k], path[k + 1]
                                        edge_pair = frozenset([u, v])
                                        if edge_pair in completed_pairs:
                                            completed_road_count += 1
                                        path_length += graph[u][v].get('length', 1)
                                    
                                    # Prefer paths with fewer completed roads
                                    path_score = path_length * (1 + completed_road_count)
                                    if path_score < min_path_length:
                                        min_path_length = path_score
                                        best_path = path
                                        best_source = source
                                        best_target = target
                            except Exception:
                                continue
                    
                    if best_path:
                        # Add the path, preferring uncompleted roads when available
                        for k in range(len(best_path) - 1):
                            u, v = best_path[k], best_path[k + 1]
                            if not filtered_graph.has_edge(u, v):
                                edge_data = copy_edge_data(graph.get_edge_data(u, v))
                                filtered_graph.add_edge(u, v, **edge_data)
                                # Add reverse edge to maintain balance
                                if graph.has_edge(v, u):
                                    edge_data = copy_edge_data(graph.get_edge_data(v, u))
                                    filtered_graph.add_edge(v, u, **edge_data)
                        logger.info(f"Connected components using mixed road path: {best_source}->{best_target}")
                    else:
                        logger.warning(f"Could not find path between components {i} and {j}")
        
        # Final balance check
        unbalanced_nodes = []
        for node in filtered_graph.nodes():
            in_degree = filtered_graph.in_degree(node)
            out_degree = filtered_graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            logger.error(f"Graph is still unbalanced after connecting components. Found {len(unbalanced_nodes)} unbalanced nodes")
            # Try to balance each component separately
            components = list(nx.weakly_connected_components(filtered_graph))
            logger.info(f"Attempting to balance {len(components)} components separately")
            
            for i, component in enumerate(components):
                subgraph = filtered_graph.subgraph(component).copy()
                needs_in = []
                needs_out = []
                
                # Find imbalanced nodes in this component
                for node in subgraph.nodes():
                    in_deg = subgraph.in_degree(node)
                    out_deg = subgraph.out_degree(node)
                    if in_deg < out_deg:
                        needs_in.extend([(node, out_deg - in_deg)])
                    elif in_deg > out_deg:
                        needs_out.extend([(node, in_deg - out_deg)])
                
                # Try to balance within component
                if needs_in and needs_out:
                    logger.info(f"Component {i}: {len(needs_in)} nodes need in, {len(needs_out)} need out")
                    for target_node, needed in needs_in:
                        for source_node, available in needs_out:
                            if needed > 0 and available > 0:
                                try:
                                    # Try to find a path through uncompleted roads first
                                    path = nx.shortest_path(subgraph, source_node, target_node)
                                    edge_data = copy_edge_data(graph.get_edge_data(path[0], path[1]))
                                    filtered_graph.add_edge(source_node, target_node, **edge_data)
                                    needed -= 1
                                    available -= 1
                                except nx.NetworkXNoPath:
                                    continue
            
            # Check if we managed to balance the components
            still_unbalanced = []
            for node in filtered_graph.nodes():
                in_deg = filtered_graph.in_degree(node)
                out_deg = filtered_graph.out_degree(node)
                if in_deg != out_deg:
                    still_unbalanced.append((node, in_deg, out_deg))
            
            if still_unbalanced:
                logger.error(f"Could not balance components. {len(still_unbalanced)} nodes remain unbalanced")
                return graph
        
        # Verify connectivity - but don't revert if disconnected
        if not nx.is_weakly_connected(filtered_graph):
            components = list(nx.weakly_connected_components(filtered_graph))
            logger.warning(f"Graph has {len(components)} disconnected components")
            
            # Find the largest component
            largest_component = max(components, key=len)
            logger.info(f"Using largest component with {len(largest_component)} nodes")
            
            # Create a new graph with just the largest component
            largest_graph = filtered_graph.subgraph(largest_component).copy()
            
            # Verify the largest component is balanced
            unbalanced = False
            for node in largest_graph.nodes():
                if largest_graph.in_degree(node) != largest_graph.out_degree(node):
                    unbalanced = True
                    break
            
            if unbalanced:
                logger.error("Largest component is not balanced, reverting to original graph")
                return graph
            
            filtered_graph = largest_graph
            logger.info(f"Proceeding with largest balanced component")
        
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
            list: The Eulerian circuit as a list of node pairs
        """
        logger.info('Starting to find Eulerian circuit in directed graph')
        
        # Filter out completed roads if requested
        if completed_area is not None:
            graph = self.filter_completed_roads(graph, completed_area)
            logger.info(f"Working with filtered graph containing {graph.number_of_edges()} edges")
        
        # If no start node specified, use any node
        if start_node is None:
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
        
        # Find Eulerian circuit
        try:
            # For directed graphs, we use nx.eulerian_circuit directly
            self.euler_circuit = list(nx.eulerian_circuit(graph, source=start_node))
            logger.info(f"Found initial Eulerian circuit with {len(self.euler_circuit)} edges")
            
            # Verify all edges are included
            circuit_edges = set((u,v) for u,v in self.euler_circuit)
            all_edges = set(graph.edges())
            missing_edges = all_edges - circuit_edges
            
            if missing_edges:
                logger.error(f"Circuit is incomplete. Missing {len(missing_edges)} edges:")
                for edge in missing_edges:
                    logger.error(f"Missing edge: {edge}")
                raise ValueError(f"Circuit is incomplete. Missing {len(missing_edges)} edges.")
            
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
        Create a GPX track with direction indicators.
        
        Args:
            graph (nx.DiGraph): The graph containing the edges
            edges (list): List of edges to include in the track
            simplify (bool): Whether to simplify the resulting GPX
            
        Returns:
            str: The filename of the created GPX file
        """
        stats_distance = 0.0
        stats_backtrack = 0.0
        total_direction_markers = 0

        logger.info('Creating GPX track with direction indicators')
        logger.info(f'Number of edges to process: {len(edges)}')

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'optiburb_route'
        gpx.author_name = 'optiburb'
        gpx.creator = 'OptiburB Route Generator'
        gpx.description = 'Generated route with direction indicators'
        gpx.keywords = 'directed route,one-way streets'

        track = gpxpy.gpx.GPXTrack()
        track.name = 'optiburb_track'
        track.type = 'directed'
        gpx.tracks.append(track)

        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)

        i = 1
        arrow_interval = 3  # Add direction arrow every 3 points
        logger.info(f'Using arrow interval of {arrow_interval} points')

        for n, edge in enumerate(edges):
            u, v = edge
            edge_data = graph.get_edge_data(u, v)

            logger.debug('EDGE [%d] - edge=%s, data=%s', n, edge, edge_data)

            if edge_data is None:
                logger.warning('null data for edge %s', edge)
                try:
                    u_coords = (graph.nodes[u]['x'], graph.nodes[u]['y'])
                    v_coords = (graph.nodes[v]['x'], graph.nodes[v]['y'])
                    markers_added = self._add_track_points(segment, [u_coords, v_coords], i, arrow_interval)
                    if markers_added is not None:
                        total_direction_markers += markers_added
                    i += 2
                except (KeyError, AttributeError) as e:
                    logger.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                continue

            linestring = edge_data.get('geometry')
            augmented = edge_data.get('augmented')
            stats_distance += edge_data.get('length', 0)

            logger.debug(' leg [%d] -> %s (%s,%s,%s,%s,%s)', n, edge_data.get('name', ''), 
                        edge_data.get('highway', ''), edge_data.get('surface', ''), 
                        edge_data.get('oneway', ''), edge_data.get('access', ''), 
                        edge_data.get('length', 0))

            coords_to_use = None
            if linestring:
                try:
                    # Extract coordinates from LineString
                    if hasattr(linestring, 'coords'):
                        coords_list = list(linestring.coords)
                        if coords_list:
                            coords_to_use = coords_list
                            logger.debug(f'Extracted {len(coords_to_use)} coordinates from LineString')
                    
                    # If we couldn't get coords directly, try directional linestring
                    if not coords_to_use:
                        directional_coords = self.geometry.get_directional_linestring(edge, linestring, graph.nodes)
                        if directional_coords:
                            coords_to_use = directional_coords
                            logger.debug(f'Using directional linestring with {len(coords_to_use)} points')
                except Exception as e:
                    logger.error(f"Error extracting coordinates from LineString: {str(e)}")

            if coords_to_use is None:
                try:
                    u_coords = (graph.nodes[u]['x'], graph.nodes[u]['y'])
                    v_coords = (graph.nodes[v]['x'], graph.nodes[v]['y'])
                    coords_to_use = [u_coords, v_coords]
                    logger.debug(f"Using straight line for edge {edge}")
                except (KeyError, AttributeError) as e:
                    logger.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                    continue

            if not coords_to_use:
                logger.error(f"No valid coordinates found for edge {edge}")
                continue

            markers_added = self._add_track_points(segment, coords_to_use, i, arrow_interval)
            if markers_added is not None:
                total_direction_markers += markers_added
            i += len(coords_to_use)

            if edge_data.get('augmented', False):
                stats_backtrack += edge_data.get('length', 0)

        # Verify we have points in the segment
        if not segment.points:
            raise ValueError("No valid points were added to the GPX track")

        logger.info('total distance = %.2fkm', stats_distance/1000.0)
        logger.info('backtrack distance = %.2fkm', stats_backtrack/1000.0)
        logger.info(f'Total direction markers added to GPX: {total_direction_markers}')
        
        if simplify:
            logger.info('simplifying GPX')
            # Store direction markers before simplification
            direction_markers = []
            for point in segment.points:
                if hasattr(point, 'type') and point.type == 'direction':
                    direction_markers.append(point)
            
            # Remove direction markers temporarily
            segment.points = [p for p in segment.points if not (hasattr(p, 'type') and p.type == 'direction')]
            
            # Simplify the track
            gpx.simplify()
            
            # Re-add direction markers at appropriate intervals
            simplified_points = segment.points[:]
            segment.points = []
            
            arrow_interval = max(3, len(simplified_points) // (total_direction_markers + 1))
            for i, point in enumerate(simplified_points):
                segment.points.append(point)
                if i > 0 and i < len(simplified_points) - 1 and i % arrow_interval == 0:
                    # Create new direction marker
                    marker = gpxpy.gpx.GPXTrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude
                    )
                    marker.type = 'direction'
                    marker.symbol = '➜'
                    # Calculate bearing to next point
                    next_point = simplified_points[i + 1]
                    bearing = self.geometry.calculate_bearing(
                        point.latitude, point.longitude,
                        next_point.latitude, next_point.longitude
                    )
                    marker.comment = str(round(bearing, 1))
                    segment.points.append(marker)

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