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
        uncompleted_edges = set()  # Single direction edges that need to be preserved
        nodes_with_uncompleted = set()  # Nodes that have uncompleted edges
        
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
                            # Only one direction completed, treat as uncompleted
                            uncompleted_edges.add((u, v))
                            nodes_with_uncompleted.add(u)
                            nodes_with_uncompleted.add(v)
                    else:
                        # Single direction completed edge
                        completed_pairs[edge_pair] = ((u, v), None)
                else:
                    # Edge is not completed, keep only one direction unless both are needed
                    if (v, u) not in uncompleted_edges:  # Only add if reverse not already added
                        uncompleted_edges.add((u, v))
                        nodes_with_uncompleted.add(u)
                        nodes_with_uncompleted.add(v)
            else:
                # Edge is not completed, keep only one direction unless both are needed
                if (v, u) not in uncompleted_edges:  # Only add if reverse not already added
                    uncompleted_edges.add((u, v))
                    nodes_with_uncompleted.add(u)
                    nodes_with_uncompleted.add(v)

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
            edge_data = graph.get_edge_data(u, v)
            uncompleted_graph.add_edge(u, v, **edge_data)
        
        # Find weakly connected components (regions of uncompleted edges)
        uncompleted_components = list(nx.weakly_connected_components(uncompleted_graph))
        logger.info(f"Found {len(uncompleted_components)} regions of uncompleted edges")
        
        # Create filtered graph starting with uncompleted edges
        filtered_graph = nx.DiGraph()
        
        # Add all uncompleted edges first
        for u, v in uncompleted_edges:
            edge_data = graph.get_edge_data(u, v)
            filtered_graph.add_edge(u, v, **edge_data)
        
        # For each pair of components, find the shortest connecting path
        paths_to_add = set()
        for i in range(len(uncompleted_components)):
            for j in range(i + 1, len(uncompleted_components)):
                comp1 = uncompleted_components[i]
                comp2 = uncompleted_components[j]
                
                # Find the shortest path between any pair of nodes in the two components
                min_path_length = float('inf')
                best_path = None
                
                for source in comp1:
                    for target in comp2:
                        try:
                            path = nx.shortest_path(graph, source, target, weight='length')
                            path_length = sum(graph[path[k]][path[k+1]].get('length', 1) 
                                            for k in range(len(path)-1))
                            
                            if path_length < min_path_length:
                                min_path_length = path_length
                                best_path = path
                        except nx.NetworkXNoPath:
                            continue
                
                if best_path:
                    # Add edges from the shortest path
                    for k in range(len(best_path) - 1):
                        u, v = best_path[k], best_path[k + 1]
                        edge_pair = frozenset([u, v])
                        
                        # If this is a completed pair, add both directions to maintain balance
                        if edge_pair in completed_pairs:
                            if completed_pairs[edge_pair][1]:  # If it has a reverse edge
                                paths_to_add.add(completed_pairs[edge_pair][0])
                                paths_to_add.add(completed_pairs[edge_pair][1])
                            else:
                                paths_to_add.add(completed_pairs[edge_pair][0])
                        else:
                            paths_to_add.add((u, v))
        
        # Add all required paths
        for u, v in paths_to_add:
            if not filtered_graph.has_edge(u, v):
                edge_data = graph.get_edge_data(u, v)
                filtered_graph.add_edge(u, v, **edge_data)
        
        # Verify balance
        unbalanced_nodes = []
        for node in filtered_graph.nodes():
            in_degree = filtered_graph.in_degree(node)
            out_degree = filtered_graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            logger.warning(f"Found {len(unbalanced_nodes)} unbalanced nodes after filtering")
            
            # Create lists of nodes needing incoming and outgoing edges
            needs_in = []  # (node, num_edges_needed)
            needs_out = []  # (node, num_edges_needed)
            
            for node, in_deg, out_deg in unbalanced_nodes:
                if in_deg < out_deg:
                    needs_in.append((node, out_deg - in_deg))
                else:
                    needs_out.append((node, in_deg - out_deg))
            
            logger.info(f"Nodes needing incoming edges: {len(needs_in)}")
            logger.info(f"Nodes needing outgoing edges: {len(needs_out)}")
            
            # Create a flow network to solve the balancing problem
            flow_graph = nx.DiGraph()
            
            # Add source and sink nodes
            source = 'source'
            sink = 'sink'
            
            # Add edges from source to nodes needing outgoing edges
            for node, deficit in needs_out:
                flow_graph.add_edge(source, node, capacity=deficit)
            
            # Add edges from nodes needing incoming edges to sink
            for node, deficit in needs_in:
                flow_graph.add_edge(node, sink, capacity=deficit)
            
            # Add edges between all pairs of unbalanced nodes if a path exists in original graph
            for source_node, _ in needs_out:
                for target_node, _ in needs_in:
                    try:
                        # Find shortest path in original graph
                        path = nx.shortest_path(graph, source_node, target_node, weight='length')
                        path_length = sum(graph[path[i]][path[i+1]].get('length', 1) 
                                        for i in range(len(path)-1))
                        
                        # Add edge to flow graph with high capacity
                        flow_graph.add_edge(source_node, target_node, 
                                          capacity=10,  # Allow multiple flows through this path
                                          path=path,
                                          length=path_length)
                    except nx.NetworkXNoPath:
                        continue
            
            try:
                # Find the minimum cost flow
                flow_dict = nx.max_flow_min_cost(flow_graph, 'source', 'sink')
                
                # Add balancing edges based on the flow
                edges_added = 0
                for u in flow_dict:
                    if u not in ('source', 'sink'):
                        for v, flow in flow_dict[u].items():
                            if v not in ('source', 'sink') and flow > 0:
                                # Get the path from the flow graph
                                edge_data = flow_graph[u][v]
                                if 'path' in edge_data:
                                    path = edge_data['path']
                                    # Add all edges along the path
                                    for i in range(len(path)-1):
                                        source, target = path[i], path[i+1]
                                        if not filtered_graph.has_edge(source, target):
                                            # Check if this is a completed edge pair
                                            edge_pair = frozenset([source, target])
                                            if edge_pair in completed_pairs:
                                                if completed_pairs[edge_pair][1]:  # Has reverse edge
                                                    filtered_graph.add_edge(source, target, 
                                                                         **graph.get_edge_data(source, target))
                                                    filtered_graph.add_edge(target, source, 
                                                                         **graph.get_edge_data(target, source))
                                                    edges_added += 2
                                                else:
                                                    filtered_graph.add_edge(source, target, 
                                                                         **graph.get_edge_data(source, target))
                                                    edges_added += 1
                                            else:
                                                # Add the edge from the original graph
                                                filtered_graph.add_edge(source, target, 
                                                                     **graph.get_edge_data(source, target))
                                                edges_added += 1
                
                logger.info(f"Added {edges_added} edges to balance the graph")
                
                # Verify balance again
                unbalanced_after = []
                for node in filtered_graph.nodes():
                    in_degree = filtered_graph.in_degree(node)
                    out_degree = filtered_graph.out_degree(node)
                    if in_degree != out_degree:
                        unbalanced_after.append((node, in_degree, out_degree))
                
                if unbalanced_after:
                    logger.error(f"Graph is still unbalanced after adding edges. Found {len(unbalanced_after)} unbalanced nodes")
                    # Try one final balancing pass using direct edges
                    needs_in = [(n, i, o) for n, i, o in unbalanced_after if i < o]
                    needs_out = [(n, i, o) for n, i, o in unbalanced_after if i > o]
                    
                    while needs_in and needs_out:
                        target, t_in, t_out = needs_in[0]
                        source, s_in, s_out = needs_out[0]
                        
                        # Add direct edge
                        if not filtered_graph.has_edge(source, target):
                            # Try to use existing edge data
                            if graph.has_edge(source, target):
                                filtered_graph.add_edge(source, target, **graph.get_edge_data(source, target))
                            else:
                                # Create new edge with straight line geometry
                                edge_data = {}
                                source_coords = (graph.nodes[source]['x'], graph.nodes[source]['y'])
                                target_coords = (graph.nodes[target]['x'], graph.nodes[target]['y'])
                                edge_data['geometry'] = shapely.geometry.LineString([source_coords, target_coords])
                                filtered_graph.add_edge(source, target, **edge_data)
                            edges_added += 1
                        
                        # Update counts and remove balanced nodes
                        if t_out - t_in == 1:
                            needs_in.pop(0)
                        if s_in - s_out == 1:
                            needs_out.pop(0)
                    
                    # Final balance check
                    unbalanced_final = []
                    for node in filtered_graph.nodes():
                        in_degree = filtered_graph.in_degree(node)
                        out_degree = filtered_graph.out_degree(node)
                        if in_degree != out_degree:
                            unbalanced_final.append((node, in_degree, out_degree))
                    
                    if unbalanced_final:
                        logger.error(f"Failed to balance graph. Found {len(unbalanced_final)} unbalanced nodes after all attempts")
                        return graph
            
            except nx.NetworkXUnfeasible:
                logger.error("No feasible flow found to balance the graph")
                return graph
        
        # Verify connectivity
        if not nx.is_weakly_connected(filtered_graph):
            logger.warning("Graph became disconnected after filtering, reverting changes")
            return graph
        
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