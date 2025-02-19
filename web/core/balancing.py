"""
Graph Balancer Module

This module handles graph balancing operations for the OptiburB system,
including node balancing, dead end optimization, and finding connecting edges.
"""

import networkx as nx
import shapely.geometry
from web.utils.logging import logger

class GraphBalancer:
    def __init__(self, geometry_manager):
        """
        Initialize the GraphBalancer.
        
        Args:
            geometry_manager (GeometryManager): Instance of GeometryManager for geometry operations
        """
        self.geometry = geometry_manager

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
        
        # Add debug check at the start
        self._debug_edge_attributes(graph)
        
        edges_added = 0
        max_iterations = 100  # Prevent infinite loops
        iteration = 0
        
        # Find weakly connected components
        components = list(nx.weakly_connected_components(graph))
        if len(components) > 1:
            logger.warning(f'Graph has {len(components)} disconnected components')
            # Log the size of each component
            for i, comp in enumerate(components):
                logger.info(f'Component {i}: {len(comp)} nodes')
            
            # Find edges needed to connect components
            connecting_edges = self._find_connecting_edges(graph, components, node_coords)
            
            # Add the connecting edges to our graph
            for u, v, data in connecting_edges:
                # Add edge in both directions to ensure connectivity
                graph.add_edge(u, v, **data)
                # Add reverse edge with same data but reversed geometry
                reverse_data = dict(data)
                if 'geometry' in reverse_data:
                    reverse_data['geometry'] = shapely.geometry.LineString(
                        list(reverse_data['geometry'].coords)[::-1]
                    )
                graph.add_edge(v, u, **reverse_data)
                logger.debug(f'Added connecting edges {u}-{v} and {v}-{u} with geometry')
        
        # For each node, ensure in-degree equals out-degree
        nodes_balanced = 0
        edges_added = 0
        
        for node in graph.nodes():
            in_degree = graph.in_degree(node)
            out_degree = graph.out_degree(node)
            
            if in_degree != out_degree:
                logger.info(f"Node {node} has imbalanced degrees: in={in_degree}, out={out_degree}")
                # Add necessary edges to balance the node
                if in_degree > out_degree:
                    # Need more outgoing edges
                    for _ in range(in_degree - out_degree):
                        # Find a reachable node we can connect to
                        for target in graph.nodes():
                            if target != node and not graph.has_edge(node, target):
                                # Try to find a path to this node
                                try:
                                    path = nx.shortest_path(graph, node, target, weight='length')
                                    # Create edge following this path
                                    if self._add_path_as_edge(graph, node, target, path, node_coords):
                                        edges_added += 1
                                        break
                                except nx.NetworkXNoPath:
                                    continue
                elif out_degree > in_degree:
                    # Need more incoming edges
                    for _ in range(out_degree - in_degree):
                        # Find a node that can reach us
                        for source in graph.nodes():
                            if source != node and not graph.has_edge(source, node):
                                try:
                                    path = nx.shortest_path(graph, source, node, weight='length')
                                    if self._add_path_as_edge(graph, source, node, path, node_coords):
                                        edges_added += 1
                                        break
                                except nx.NetworkXNoPath:
                                    continue
                
                # Verify the node is now balanced
                new_in_degree = graph.in_degree(node)
                new_out_degree = graph.out_degree(node)
                if new_in_degree == new_out_degree:
                    nodes_balanced += 1
                else:
                    logger.error(f"Failed to balance node {node}: in={new_in_degree}, out={new_out_degree}")
        
        logger.info(f"Balanced {nodes_balanced} nodes by adding {edges_added} edges")
        
        # Verify the graph is balanced
        unbalanced_nodes = [(node, graph.in_degree(node), graph.out_degree(node))
                           for node in graph.nodes()
                           if graph.in_degree(node) != graph.out_degree(node)]
        
        if unbalanced_nodes:
            logger.error("Graph is still not balanced after processing:")
            for node, in_deg, out_deg in unbalanced_nodes:
                logger.error(f"Node {node}: in={in_deg}, out={out_deg}")
            raise ValueError("Failed to create a balanced directed graph")
        
        return graph

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
        
        # Find dead ends (nodes with total degree of 1)
        deadends = set()
        for node in graph.nodes():
            in_degree = graph.in_degree(node)
            out_degree = graph.out_degree(node)
            if in_degree + out_degree == 1:
                deadends.add(node)
                logger.info(f"Found dead end at node {node}: in={in_degree}, out={out_degree}")
        
        if not deadends:
            logger.info("No dead ends found in graph")
            return graph
        
        logger.info(f"Found {len(deadends)} dead ends to optimize")
        edges_added = 0
        
        for deadend in deadends:
            # Check incoming edges
            in_edges = list(graph.in_edges(deadend, data=True))
            # Check outgoing edges
            out_edges = list(graph.out_edges(deadend, data=True))
            
            if len(in_edges) + len(out_edges) != 1:
                logger.error(f'Wrong number of edges for dead-end node {deadend}')
                continue
            
            # If we have an incoming edge, add a return edge
            if in_edges:
                source, target, data = in_edges[0]
                if not graph.has_edge(target, source):
                    edge_data = dict(data)
                    edge_data['augmented'] = True
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        # Reverse the geometry for the return edge
                        edge_data['geometry'] = shapely.geometry.LineString(
                            list(edge_data['geometry'].coords)[::-1]
                        )
                    graph.add_edge(target, source, **edge_data)
                    edges_added += 1
                    logger.info(f"Added return edge for dead end: {target}->{source}")
            
            # If we have an outgoing edge, add a return edge
            if out_edges:
                source, target, data = out_edges[0]
                if not graph.has_edge(target, source):
                    edge_data = dict(data)
                    edge_data['augmented'] = True
                    if 'geometry' in edge_data and edge_data['geometry'] is not None:
                        # Reverse the geometry for the return edge
                        edge_data['geometry'] = shapely.geometry.LineString(
                            list(edge_data['geometry'].coords)[::-1]
                        )
                    graph.add_edge(target, source, **edge_data)
                    edges_added += 1
                    logger.info(f"Added return edge for dead end: {target}->{source}")
        
        logger.info(f"Added {edges_added} return edges for dead ends")
        
        # Verify the graph remains balanced
        unbalanced_nodes = []
        for node in graph.nodes():
            in_degree = graph.in_degree(node)
            out_degree = graph.out_degree(node)
            if in_degree != out_degree:
                unbalanced_nodes.append((node, in_degree, out_degree))
        
        if unbalanced_nodes:
            logger.error("Graph is not balanced after dead end optimization:")
            for node, in_deg, out_deg in unbalanced_nodes:
                logger.error(f"Node {node}: in={in_deg}, out={out_deg}")
            raise ValueError("Failed to maintain balance during dead end optimization")
        
        return graph

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