"""
Balancing Module

This module handles graph balancing operations for the OptiburB system.
"""

import networkx as nx
import shapely.geometry
from web.utils.logging import logger
import copy

class GraphBalancer:
    def __init__(self, geometry_manager):
        """
        Initialize the GraphBalancer.
        
        Args:
            geometry_manager (GeometryManager): Instance of GeometryManager for geometry operations
        """
        self.geometry = geometry_manager

    def _copy_graph(self, graph):
        """
        Create a deep copy of a graph with proper handling of edge attributes.
        
        Args:
            graph (nx.DiGraph): The graph to copy
            
        Returns:
            nx.DiGraph: A new graph with copied nodes and edges
        """
        new_graph = nx.DiGraph()
        
        # Count edges with geometry in original graph
        orig_edges_with_geom = sum(1 for _, _, data in graph.edges(data=True) if 'geometry' in data)
        logger.info(f"Original graph has {graph.number_of_edges()} edges, {orig_edges_with_geom} with geometry")
        
        # Copy nodes with their attributes
        for node, data in graph.nodes(data=True):
            new_graph.add_node(node, **copy.deepcopy(data))
        
        # Copy edges with their attributes
        edges_with_geom = 0
        for u, v, data in graph.edges(data=True):
            # Deep copy the edge data
            edge_data = copy.deepcopy(data)
            
            # Special handling for geometry objects
            if 'geometry' in data:
                edges_with_geom += 1
                if isinstance(data['geometry'], shapely.geometry.LineString):
                    edge_data['geometry'] = shapely.geometry.LineString(list(data['geometry'].coords))
                    if 'length' not in edge_data:
                        edge_data['length'] = edge_data['geometry'].length
            
            new_graph.add_edge(u, v, **edge_data)
        
        logger.info(f"Copied graph has {new_graph.number_of_edges()} edges, {edges_with_geom} with geometry")
        if edges_with_geom != orig_edges_with_geom:
            logger.warning(f"Lost geometry information during graph copying!")
        
        return new_graph

    def _ensure_connectivity(self, graph, node_coords):
        """
        Ensure the graph is connected by adding edges between disconnected components.
        
        Args:
            graph (nx.DiGraph): The graph to connect
            node_coords (dict): Dictionary of node coordinates
            
        Returns:
            nx.DiGraph: The connected graph
        """
        # Find weakly connected components
        components = list(nx.weakly_connected_components(graph))
        if len(components) <= 1:
            return graph
            
        logger.warning(f"Found {len(components)} disconnected components")
        
        # Create a working copy
        working_graph = graph.copy()
        
        # For each component, find the closest node in any other component
        while len(components) > 1:
            min_distance = float('inf')
            best_connection = None
            
            for i, comp1 in enumerate(components[:-1]):
                for comp2 in components[i+1:]:
                    for node1 in comp1:
                        coords1 = node_coords[node1]
                        for node2 in comp2:
                            coords2 = node_coords[node2]
                            distance = self.geometry.calculate_distance(coords1, coords2)
                            if distance < min_distance:
                                min_distance = distance
                                best_connection = (node1, node2, distance)
            
            if best_connection:
                node1, node2, distance = best_connection
                # Add bidirectional edges between the closest nodes
                edge_data = {
                    'geometry': shapely.geometry.LineString([node_coords[node1], node_coords[node2]]),
                    'length': distance,
                    'connecting': True,  # Mark this as a connecting edge
                    'is_straight_line': True  # Mark as straight line
                }
                working_graph.add_edge(node1, node2, **edge_data)
                # Add reverse edge with reversed geometry
                reverse_data = edge_data.copy()
                reverse_data['geometry'] = shapely.geometry.LineString([node_coords[node2], node_coords[node1]])
                working_graph.add_edge(node2, node1, **reverse_data)
                
                # Log detailed information about the connection
                node1_info = working_graph.nodes[node1]
                node2_info = working_graph.nodes[node2]
                logger.debug(f"STRAIGHT_LINE: Connected components with straight line between:")
                logger.debug(f"  Node1 ({node1}): {node1_info.get('name', 'unnamed')} at ({node_coords[node1][0]:.6f}, {node_coords[node1][1]:.6f})")
                logger.debug(f"  Node2 ({node2}): {node2_info.get('name', 'unnamed')} at ({node_coords[node2][0]:.6f}, {node_coords[node2][1]:.6f})")
                logger.debug(f"  Distance: {distance:.2f}, Edge attributes: {edge_data}")
                
                # Recompute components
                components = list(nx.weakly_connected_components(working_graph))
            else:
                logger.error("Failed to find connecting nodes between components")
                break
        
        return working_graph

    def balance_graph(self, graph, node_coords):
        """
        Balance the graph by ensuring in-degree equals out-degree for all nodes.
        
        Args:
            graph (nx.DiGraph): The graph to balance
            node_coords (dict): Dictionary of node coordinates
            
        Returns:
            nx.DiGraph: The balanced graph
        """
        logger.info('Processing directed graph for balancing')
        
        # First ensure the graph is connected
        working_graph = self._ensure_connectivity(graph, node_coords)
        
        # Count edges with geometry at start
        edges_with_geom = sum(1 for _, _, data in working_graph.edges(data=True) if 'geometry' in data)
        straight_lines = sum(1 for _, _, data in working_graph.edges(data=True) if data.get('is_straight_line', False))
        logger.info(f"Initial graph state: {working_graph.number_of_edges()} total edges, {edges_with_geom} with geometry, {straight_lines} straight lines")
        
        # Log all straight line edges at start
        logger.debug("Initial straight line edges:")
        for u, v, data in working_graph.edges(data=True):
            if data.get('is_straight_line', False):
                u_info = working_graph.nodes[u]
                v_info = working_graph.nodes[v]
                logger.debug(f"STRAIGHT_LINE: {u}->{v}")
                logger.debug(f"  From: {u_info.get('name', 'unnamed')} at ({node_coords[u][0]:.6f}, {node_coords[u][1]:.6f})")
                logger.debug(f"  To: {v_info.get('name', 'unnamed')} at ({node_coords[v][0]:.6f}, {node_coords[v][1]:.6f})")
                logger.debug(f"  Edge attributes: {data}")
        
        # Validate node IDs
        invalid_nodes = []
        for node in working_graph.nodes():
            if not isinstance(node, (int, str)):
                invalid_nodes.append(node)
                logger.error(f"Invalid node ID type: {type(node)} for node {node}")
        
        if invalid_nodes:
            raise ValueError(f"Found {len(invalid_nodes)} invalid node IDs. Node IDs must be integers or strings.")
        
        # First, identify all unbalanced nodes and their degrees
        unbalanced_nodes = []
        for node in working_graph.nodes():
            in_degree = working_graph.in_degree(node)
            out_degree = working_graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
                logger.info(f"Initial imbalance - Node {node}: in={in_degree}, out={out_degree}")

        if not unbalanced_nodes:
            logger.info("Graph is already balanced")
            return working_graph

        # Group nodes by whether they need incoming or outgoing edges
        needs_incoming = []  # nodes where in_degree < out_degree
        needs_outgoing = []  # nodes where in_degree > out_degree
        
        for node, in_degree, out_degree in unbalanced_nodes:
            if in_degree < out_degree:
                needs_incoming.extend([(node, out_degree - in_degree)])
            else:
                needs_outgoing.extend([(node, in_degree - out_degree)])

        # Sort by the number of edges needed (descending)
        needs_incoming.sort(key=lambda x: x[1], reverse=True)
        needs_outgoing.sort(key=lambda x: x[1], reverse=True)

        logger.info(f"Nodes needing incoming edges: {len(needs_incoming)}")
        logger.info(f"Nodes needing outgoing edges: {len(needs_outgoing)}")

        edges_added = 0
        max_iterations = 1000
        iteration = 0

        while needs_incoming and needs_outgoing and iteration < max_iterations:
            target_node, target_needed = needs_incoming[0]
            source_node, source_needed = needs_outgoing[0]

            try:
                # Find shortest path between nodes
                path = nx.shortest_path(working_graph, source_node, target_node)
                
                # Create edge data by combining geometries along the path
                edge_data = {}
                path_coords = []
                path_length = 0
                template_data = None
                
                # Collect all edge data along the path
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    edge = working_graph.get_edge_data(u, v)
                    if edge:
                        # Store the first edge data as a template
                        if template_data is None:
                            template_data = {}
                            # Copy only string attributes
                            for key, value in edge.items():
                                if isinstance(key, str) and key not in ('geometry', 'length', 'is_straight_line'):
                                    template_data[key] = str(value) if value is not None else ''
                            edge_data.update(template_data)
                        
                        # Handle geometry and length separately
                        if 'geometry' in edge:
                            path_coords.extend(list(edge['geometry'].coords))
                            path_length += edge.get('length', 0)

                # Create geometry for the new edge
                if path_coords:
                    edge_data['geometry'] = shapely.geometry.LineString(path_coords)
                    edge_data['length'] = float(path_length)
                else:
                    # If no geometry found along path, create straight line
                    u_coords = node_coords[source_node]
                    v_coords = node_coords[target_node]
                    edge_data['geometry'] = shapely.geometry.LineString([u_coords, v_coords])
                    edge_data['length'] = float(self.geometry.calculate_distance(u_coords, v_coords))
                    edge_data['is_straight_line'] = True
                    logger.debug(f"Created straight line geometry for {source_node}->{target_node}")

                # Add balancing edge with complete edge data
                working_graph.add_edge(source_node, target_node, **edge_data)
                edges_added += 1

                # Update counts and remove balanced nodes
                needs_incoming[0] = (target_node, target_needed - 1)
                needs_outgoing[0] = (source_node, source_needed - 1)

                if target_needed == 1:
                    needs_incoming.pop(0)
                if source_needed == 1:
                    needs_outgoing.pop(0)

            except nx.NetworkXNoPath:
                logger.warning(f"No path found between {source_node} and {target_node}")
                # Create direct edge with straight line geometry
                edge_data = {}
                
                # Find a template edge to copy attributes from
                template_edge = None
                for u, v, data in working_graph.edges(data=True):
                    if 'geometry' in data:
                        # Copy only string attributes
                        for key, value in data.items():
                            if isinstance(key, str) and key not in ('geometry', 'length', 'is_straight_line'):
                                edge_data[key] = str(value) if value is not None else ''
                        break
                
                # Add geometry and length
                u_coords = node_coords[source_node]
                v_coords = node_coords[target_node]
                edge_data['geometry'] = shapely.geometry.LineString([u_coords, v_coords])
                edge_data['length'] = float(self.geometry.calculate_distance(u_coords, v_coords))
                edge_data['is_straight_line'] = True
                
                working_graph.add_edge(source_node, target_node, **edge_data)
                edges_added += 1

                needs_incoming[0] = (target_node, target_needed - 1)
                needs_outgoing[0] = (source_node, source_needed - 1)

                if target_needed == 1:
                    needs_incoming.pop(0)
                if source_needed == 1:
                    needs_outgoing.pop(0)

            iteration += 1

        # Count edges with geometry after balancing
        edges_with_geom_after = sum(1 for _, _, data in working_graph.edges(data=True) if 'geometry' in data)
        logger.info(f"After balancing: {working_graph.number_of_edges()} total edges, {edges_with_geom_after} with geometry")
        logger.info(f"Added {edges_added} balancing edges in {iteration} iterations")
        
        # Count and log final straight line edges
        final_straight_lines = sum(1 for _, _, data in working_graph.edges(data=True) if data.get('is_straight_line', False))
        logger.info(f"Final graph state: added {final_straight_lines - straight_lines} new straight line edges")
        logger.debug("Final straight line edges:")
        for u, v, data in working_graph.edges(data=True):
            if data.get('is_straight_line', False):
                u_info = working_graph.nodes[u]
                v_info = working_graph.nodes[v]
                logger.debug(f"STRAIGHT_LINE: {u}->{v}")
                logger.debug(f"  From: {u_info.get('name', 'unnamed')} at ({node_coords[u][0]:.6f}, {node_coords[u][1]:.6f})")
                logger.debug(f"  To: {v_info.get('name', 'unnamed')} at ({node_coords[v][0]:.6f}, {node_coords[v][1]:.6f})")
                logger.debug(f"  Edge attributes: {data}")
        
        return working_graph

    def optimize_dead_ends(self, graph, node_coords):
        """
        Optimize dead-end roads by adding return edges.
        
        Args:
            graph (nx.DiGraph): The graph to optimize
            node_coords (dict): Dictionary of node coordinates
            
        Returns:
            nx.DiGraph: The optimized graph
        """
        logger.info('Optimizing dead-end roads in directed graph')
        
        # Create a working copy of the graph
        working_graph = self._copy_graph(graph)
        
        # Find dead ends (nodes with total degree of 1)
        deadends = set()
        for node in working_graph.nodes():
            in_degree = working_graph.in_degree(node)
            out_degree = working_graph.out_degree(node)
            if in_degree + out_degree == 1:
                deadends.add(node)
                logger.info(f"Found dead end at node {node}: in={in_degree}, out={out_degree}")
        
        if not deadends:
            logger.info("No dead ends found in graph")
            return working_graph
        
        logger.info(f"Found {len(deadends)} dead ends to optimize")
        edges_added = 0
        
        for deadend in deadends:
            # Check incoming edges
            in_edges = list(working_graph.in_edges(deadend, data=True))
            # Check outgoing edges
            out_edges = list(working_graph.out_edges(deadend, data=True))
            
            if len(in_edges) + len(out_edges) != 1:
                logger.error(f'Wrong number of edges for dead-end node {deadend}')
                continue
            
            # If we have an incoming edge, add a return edge
            if in_edges:
                source, target, data = in_edges[0]
                if not working_graph.has_edge(target, source):
                    edge_data = copy.deepcopy(data)
                    edge_data['augmented'] = True
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        # Reverse the geometry for the return edge
                        edge_data['geometry'] = shapely.geometry.LineString(
                            list(edge_data['geometry'].coords)[::-1]
                        )
                    working_graph.add_edge(target, source, **edge_data)
                    edges_added += 1
                    logger.info(f"Added return edge for dead end: {target}->{source}")
            
            # If we have an outgoing edge, add a return edge
            if out_edges:
                source, target, data = out_edges[0]
                if not working_graph.has_edge(target, source):
                    edge_data = copy.deepcopy(data)
                    edge_data['augmented'] = True
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        # Reverse the geometry for the return edge
                        edge_data['geometry'] = shapely.geometry.LineString(
                            list(edge_data['geometry'].coords)[::-1]
                        )
                    working_graph.add_edge(target, source, **edge_data)
                    edges_added += 1
                    logger.info(f"Added return edge for dead end: {target}->{source}")
        
        logger.info(f"Added {edges_added} return edges for dead ends")
        
        # Verify the graph remains balanced
        unbalanced_nodes = []
        for node in working_graph.nodes():
            in_degree = working_graph.in_degree(node)
            out_degree = working_graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            logger.error("Graph is not balanced after dead end optimization:")
            for node, in_deg, out_deg in unbalanced_nodes:
                logger.error(f"Node {node}: in={in_deg}, out={out_deg}")
            raise ValueError("Failed to maintain balance during dead end optimization")
        
        return working_graph

    def _find_connecting_edges(self, graph, components, node_coords):
        """Find the minimal set of edges needed to connect disconnected components."""
        logger.info('Finding connecting edges between components')
        
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
                                path_length, path = nx.single_source_dijkstra(graph, u, v, weight='length')
                                
                                if path_length < min_path_length:
                                    min_path_length = path_length
                                    best_path = path
                                    best_component = unconnected_idx
                                    
                                    # Get all edges along this path
                                    path_edges = []
                                    for i in range(len(path) - 1):
                                        u_path, v_path = path[i], path[i + 1]
                                        edge_data = graph.get_edge_data(u_path, v_path, 0)
                                        if edge_data:
                                            path_edges.append((u_path, v_path, dict(edge_data)))
                                    best_path_edges = path_edges
                            except nx.NetworkXNoPath:
                                continue
            
            if best_path is None:
                logger.error("Could not find connecting path for all components")
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
                            logger.info(f"Adding connecting edge {u}-{v} with real road geometry")
                        except Exception as e:
                            logger.error(f"Error copying geometry for edge {u}-{v}: {str(e)}")
                            # Fall back to straight line if geometry copy fails
                            straight_line_edges += 1
                            self._add_straight_line_edge(u, v, edge_data, edges_to_add, node_coords)
                    else:
                        # If no geometry, create a straight line
                        straight_line_edges += 1
                        self._add_straight_line_edge(u, v, edge_data, edges_to_add, node_coords)
            
            # Mark the newly connected component
            connected_components.add(best_component)
            unconnected_components.remove(best_component)
        
        # Log summary of edge additions
        if total_edges_added > 0:
            logger.warning(f"Edge connection summary:")
            logger.warning(f"  - Total edges added: {total_edges_added}")
            logger.warning(f"  - Edges using straight lines: {straight_line_edges}")
            logger.warning(f"  - Edges using real geometry: {total_edges_added - straight_line_edges}")
            if straight_line_edges > 0:
                logger.warning(f"  - {straight_line_edges} edges ({(straight_line_edges/total_edges_added)*100:.1f}%) are using straight lines!")
        
        return edges_to_add

    def _add_straight_line_edge(self, u, v, edge_data, edges_to_add, node_coords):
        """Helper method to add a straight line edge between two nodes."""
        try:
            u_coords = node_coords[u]
            v_coords = node_coords[v]
            edge_data['geometry'] = shapely.geometry.LineString([u_coords, v_coords])
            edge_data['is_straight_line'] = True  # Mark this as a straight line edge
            edges_to_add.append((u, v, edge_data))
            logger.warning(f"Created straight line geometry for edge {u}-{v} - STRAIGHT LINE WILL BE VISIBLE IN ROUTE")
        except Exception as e:
            logger.error(f"Error creating straight line edge {u}-{v}: {str(e)}")

    def _add_path_as_edge(self, graph, source, target, path, node_coords):
        """Helper method to add a new edge that follows an existing path."""
        if len(path) < 2:
            logger.error(f"Path between {source}-{target} is too short")
            return False
        
        # Check if edge already exists
        if graph.has_edge(source, target):
            logger.info(f"Edge {source}-{target} already exists, skipping")
            return False
        
        # For all paths, calculate total length and collect coordinates
        length = 0
        coords = []
        
        # Collect all coordinates and calculate total length
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if not graph.has_edge(u, v):
                logger.error(f"Missing edge {u}-{v} in working graph")
                return False
            
            edge_data = graph[u][v]
            if isinstance(edge_data, shapely.geometry.LineString):
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
                        u_coords = node_coords[u]
                        v_coords = node_coords[v]
                        geometry = shapely.geometry.LineString([u_coords, v_coords])
                        # Calculate Euclidean distance for length
                        segment_length = self.geometry.calculate_distance(u_coords, v_coords)
                        length += segment_length
                        coords.extend([u_coords, v_coords])
                        logger.warning(f"Created straight line geometry for edge {u}-{v}")
                    except (KeyError, AttributeError) as e:
                        logger.error(f"Cannot create straight line geometry for edge {u}-{v}: {str(e)}")
                        return False
                else:
                    length += edge_data.get('length', geometry.length)
                    coords.extend(list(geometry.coords))
        
        # Create the new edge with proper attributes
        if coords:
            # For MultiDiGraph, we need to pass the data as kwargs
            edge_data = {
                'geometry': shapely.geometry.LineString(coords),
                'length': length,
                'is_composite': True  # Mark this as a composite edge
            }
            graph.add_edge(source, target, **edge_data)
            logger.debug(f"Added edge {source}-{target} with length {length}")
            return True
        return False

    def _debug_edge_attributes(self, graph):
        """Debug helper to identify edges with incorrect attribute format."""
        for u, v, data in graph.edges(data=True):
            if not isinstance(data, dict):
                logger.error(f"Edge {u}-{v} has non-dictionary attributes: {type(data)}")
                # Convert LineString to proper edge attributes if needed
                if isinstance(data, shapely.geometry.LineString):
                    graph[u][v] = {
                        'geometry': data,
                        'length': data.length
                    }
                    logger.info(f"Fixed edge {u}-{v} attributes") 