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
        self.g = osmnx.simplification.simplify_graph(self.g, strict=False, remove_rings=False)
        
        # Restore coordinates after simplification
        for node in self.g.nodes():
            if node in self.node_coords:
                x, y = self.node_coords[node]
                self.g.nodes[node]['x'] = x
                self.g.nodes[node]['y'] = y
        
        # Ensure coordinates are preserved after simplification
        nodes_with_coords = sum(1 for n in self.g.nodes if 'x' in self.g.nodes[n] and 'y' in self.g.nodes[n])
        logger.info(f"After simplification:")
        logger.info(f"  - Remaining nodes: {len(self.g.nodes)}")
        logger.info(f"  - Nodes with coordinates: {nodes_with_coords}")
        
        # Update working copy
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