"""
Graph Manager Module

This module handles graph operations including loading, modifying, and managing
the road network graph for the OptiburB system.
"""

import networkx as nx
import osmnx
from web.utils.logging import logger

class GraphManager:
    def __init__(self):
        """Initialize the GraphManager."""
        self.g = None  # Main graph
        self.g_working = None  # Working copy
        self.g_augmented = None  # Augmented graph for path finding
        self.g_original = None  # Original unmodified graph
        self.node_coords = {}  # Node coordinates cache
        self.is_directed = True  # Default to directed graphs

    def load_graph(self, region, options):
        """
        Load a graph from OSM data within a region.
        
        Args:
            region (shapely.geometry.Polygon): The region to load
            options (argparse.Namespace): Configuration options
        """
        logger.info('Fetching OSM data bounded by polygon')
        
        # Get directed graph from OSM
        self.g = osmnx.graph_from_polygon(region, network_type='drive', simplify=False, 
                                        custom_filter=options.custom_filter, retain_all=True)
        
        # Ensure we have a directed graph
        if not isinstance(self.g, nx.DiGraph):
            logger.warning("Converting graph to directed type")
            self.g = nx.DiGraph(self.g)
        
        # Store original graph immediately after creation
        self.g_original = self.g.copy()
        
        # Get nodes and edges as GeoDataFrames with explicit geometry
        nodes, edges = osmnx.utils_graph.graph_to_gdfs(self.g, nodes=True, edges=True, 
                                                      node_geometry=True, fill_edge_geometry=True)
        
        # Ensure we have node coordinates
        if 'geometry' not in nodes.columns:
            logger.error("Node geometry is missing from GeoDataFrame")
            raise ValueError("Node geometry is missing")
        
        # Extract coordinates from node geometries and store them
        for node_id, node_data in nodes.iterrows():
            try:
                point = node_data['geometry']
                x, y = point.x, point.y
                self.node_coords[node_id] = (x, y)
                self.g.nodes[node_id]['x'] = x
                self.g.nodes[node_id]['y'] = y
            except Exception as e:
                logger.error(f"Could not extract coordinates for node {node_id}: {str(e)}")
        
        # Transfer edge geometries from GeoDataFrame back to graph
        edges_with_geom = 0
        straight_lines = 0
        total_edges = len(edges)
        logger.info(f"Processing {total_edges} edges from OSM data")
        
        for idx, edge_data in edges.iterrows():
            u, v, k = idx
            if 'geometry' in edge_data and edge_data['geometry'] is not None:
                # Get existing edge data
                edge_attrs = self.g.get_edge_data(u, v, k).copy()
                # Add geometry
                edge_attrs['geometry'] = edge_data['geometry']
                # Add length if not present
                if 'length' not in edge_attrs:
                    edge_attrs['length'] = edge_data['geometry'].length
                # Mark as NOT a straight line since it has real OSM geometry
                edge_attrs['is_straight_line'] = False
                # Update edge in graph
                self.g.remove_edge(u, v)
                self.g.add_edge(u, v, **edge_attrs)
                edges_with_geom += 1
                if edges_with_geom % 100 == 0:
                    logger.info(f"Processed {edges_with_geom} edges with geometry")
                logger.debug(f"Edge {u}->{v} marked with OSM geometry, is_straight_line=False")
            else:
                # If no geometry, create straight line
                try:
                    u_coords = (self.g.nodes[u]['x'], self.g.nodes[u]['y'])
                    v_coords = (self.g.nodes[v]['x'], self.g.nodes[v]['y'])
                    edge_attrs = self.g.get_edge_data(u, v, k).copy()
                    edge_attrs['geometry'] = osmnx.utils_graph.make_linestring((u_coords, v_coords))
                    edge_attrs['length'] = edge_attrs['geometry'].length
                    edge_attrs['is_straight_line'] = True  # Mark as straight line
                    self.g.remove_edge(u, v)
                    self.g.add_edge(u, v, **edge_attrs)
                    straight_lines += 1
                    logger.debug(f"Edge {u}->{v} created with straight line geometry, is_straight_line=True")
                except Exception as e:
                    logger.error(f"Could not create straight line geometry for edge {u}->{v}: {str(e)}")
        
        logger.info(f"Edge geometry statistics:")
        logger.info(f"  - Total edges: {total_edges}")
        logger.info(f"  - Edges with OSM geometry: {edges_with_geom}")
        logger.info(f"  - Edges using straight lines: {straight_lines}")
        
        # Verify edge attributes after processing
        actual_straight_lines = sum(1 for _, _, data in self.g.edges(data=True) if data.get('is_straight_line', False))
        actual_real_geom = sum(1 for _, _, data in self.g.edges(data=True) if not data.get('is_straight_line', False))
        logger.info(f"Final edge verification:")
        logger.info(f"  - Edges marked as straight lines: {actual_straight_lines}")
        logger.info(f"  - Edges marked as real geometry: {actual_real_geom}")
        
        if actual_straight_lines != straight_lines:
            logger.warning(f"Mismatch in straight line count: expected {straight_lines}, got {actual_straight_lines}")
        if actual_real_geom != edges_with_geom:
            logger.warning(f"Mismatch in real geometry count: expected {edges_with_geom}, got {actual_real_geom}")
        
        # Log coordinate statistics
        nodes_with_coords = sum(1 for n in self.g.nodes if 'x' in self.g.nodes[n] and 'y' in self.g.nodes[n])
        logger.info(f"Node coordinate statistics:")
        logger.info(f"  - Total nodes: {len(self.g.nodes)}")
        logger.info(f"  - Nodes with coordinates: {nodes_with_coords}")
        
        if nodes_with_coords < len(self.g.nodes):
            logger.warning(f"Missing coordinates for {len(self.g.nodes) - nodes_with_coords} nodes")
        
        logger.debug('original g=%s, g=%s', self.g, type(self.g))
        logger.info('original nodes=%s, edges=%s', self.g.order(), self.g.size())
        
        # Create working copy of the graph
        self.g_working = self.g.copy()
        
        # Handle simplification if requested
        if options.simplify:
            self.simplify_graph()

    def simplify_graph(self):
        """Simplify the graph by removing redundant nodes."""
        logger.info('Simplifying graph')
        
        # Count edges before simplification
        orig_straight_lines = sum(1 for _, _, data in self.g.edges(data=True) if data.get('is_straight_line', False))
        orig_real_geom = sum(1 for _, _, data in self.g.edges(data=True) if not data.get('is_straight_line', False))
        logger.info(f"Before simplification:")
        logger.info(f"  - Total edges: {self.g.number_of_edges()}")
        logger.info(f"  - Edges marked as straight lines: {orig_straight_lines}")
        logger.info(f"  - Edges with real geometry: {orig_real_geom}")
        
        # Store edge attributes before simplification
        edge_attrs = {}
        for u, v, data in self.g.edges(data=True):
            edge_attrs[(u, v)] = {
                'is_straight_line': data.get('is_straight_line', False),
                'geometry': data.get('geometry', None)
            }
            logger.debug(f"Storing attributes for edge {u}->{v}: is_straight_line={edge_attrs[(u, v)]['is_straight_line']}")
        
        # Simplify the graph
        self.g = osmnx.simplification.simplify_graph(self.g, strict=False, remove_rings=False)
        
        # Restore coordinates after simplification
        for node in self.g.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g.nodes[node]['x'] = x
                self.g.nodes[node]['y'] = y
        
        # Track new edges created during simplification
        new_edges = 0
        restored_edges = 0
        straight_lines = 0
        real_geom = 0
        
        # Restore edge attributes after simplification
        for u, v, data in self.g.edges(data=True):
            # Check if this edge existed before simplification
            if (u, v) in edge_attrs:
                data['is_straight_line'] = edge_attrs[(u, v)]['is_straight_line']
                if data['is_straight_line']:
                    straight_lines += 1
                else:
                    real_geom += 1
                restored_edges += 1
                logger.debug(f"Restored attributes for edge {u}->{v}: is_straight_line={data['is_straight_line']}")
            else:
                # If this is a new edge created during simplification, mark it based on geometry
                if 'geometry' in data:
                    data['is_straight_line'] = False
                    real_geom += 1
                    logger.debug(f"New edge {u}->{v} has geometry, marking as real road")
                else:
                    # Create straight line geometry
                    try:
                        u_coords = (self.g.nodes[u]['x'], self.g.nodes[u]['y'])
                        v_coords = (self.g.nodes[v]['x'], self.g.nodes[v]['y'])
                        data['geometry'] = osmnx.utils_graph.make_linestring((u_coords, v_coords))
                        data['length'] = data['geometry'].length
                        data['is_straight_line'] = True
                        straight_lines += 1
                        logger.debug(f"Created straight line geometry for new edge {u}->{v}")
                    except Exception as e:
                        logger.error(f"Could not create straight line geometry for edge {u}->{v}: {str(e)}")
                new_edges += 1
        
        # Count edges after simplification
        final_straight_lines = sum(1 for _, _, data in self.g.edges(data=True) if data.get('is_straight_line', False))
        final_real_geom = sum(1 for _, _, data in self.g.edges(data=True) if not data.get('is_straight_line', False))
        
        logger.info(f"After simplification:")
        logger.info(f"  - Total edges: {self.g.number_of_edges()}")
        logger.info(f"  - Restored edges: {restored_edges}")
        logger.info(f"  - New edges: {new_edges}")
        logger.info(f"  - Edges marked as straight lines: {final_straight_lines}")
        logger.info(f"  - Edges with real geometry: {final_real_geom}")
        
        if final_straight_lines != straight_lines:
            logger.warning(f"Mismatch in straight line count after simplification: counted {straight_lines}, got {final_straight_lines}")
        if final_real_geom != real_geom:
            logger.warning(f"Mismatch in real geometry count after simplification: counted {real_geom}, got {final_real_geom}")
        
        # Create working copy
        self.g_working = self.g.copy()

    def prune_graph(self):
        """Remove unwanted edges from the graph."""
        # Eliminate edges with unnamed tracks and certain highway types
        remove_types = ('track', 'path')
        removeset = set()
        
        for edge in self.g.edges:
            data = self.g.get_edge_data(*edge)
            
            if data.get('highway') in remove_types and data.get('name') is None:
                logger.debug('removing edge %s, %s', edge, data)
                removeset.add(edge)
            
            if data.get('highway') in ('cycleway',):
                logger.debug('removing edge %s, %s', edge, data)
                removeset.add(edge)
        
        # Remove the identified edges
        for edge in removeset:
            self.g.remove_edge(*edge)
        
        # Remove isolated nodes
        self.g = osmnx.utils_graph.remove_isolated_nodes(self.g)
        
        # Update working copy
        self.g_working = self.g.copy()

    def save_visualization(self, filename, odd_nodes=None):
        """
        Save a visualization of the graph.
        
        Args:
            filename (str): Output filename
            odd_nodes (set): Set of nodes to highlight
        """
        logger.info('saving SVG node file as %s', filename)
        
        if odd_nodes is None:
            odd_nodes = set()
        
        nc = ['red' if node in odd_nodes else 'blue' for node in self.g.nodes()]
        
        fig, ax = osmnx.plot_graph(self.g, show=False, save=True, 
                                  node_color=nc, filepath=filename)

    def get_edge_data(self, u, v, key=0):
        """Get data for an edge in the graph."""
        return self.g.get_edge_data(u, v, key)

    def add_edge(self, u, v, **attr):
        """Add an edge to the graph."""
        self.g.add_edge(u, v, **attr)

    def remove_edge(self, u, v):
        """Remove an edge from the graph."""
        self.g.remove_edge(u, v)

    def has_edge(self, u, v):
        """Check if an edge exists in the graph."""
        return self.g.has_edge(u, v)

    def get_node_coordinates(self, node):
        """Get the coordinates of a node."""
        return self.node_coords.get(node)

    def copy(self):
        """Create a deep copy of the current graph."""
        return self.g.copy()

    def get_nodes(self):
        """Get all nodes in the graph."""
        return self.g.nodes()

    def get_edges(self):
        """Get all edges in the graph."""
        return self.g.edges() 