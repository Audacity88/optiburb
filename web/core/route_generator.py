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

class RouteGenerator:
    def __init__(self, geometry_manager):
        """
        Initialize the RouteGenerator.
        
        Args:
            geometry_manager (GeometryManager): Instance of GeometryManager for geometry operations
        """
        self.geometry = geometry_manager
        self.euler_circuit = None

    def determine_circuit(self, graph, start_node=None):
        """
        Determine the Eulerian circuit in the directed graph.
        
        Args:
            graph (nx.DiGraph): The graph to find the circuit in
            start_node: Optional starting node
            
        Returns:
            list: The Eulerian circuit as a list of node pairs
        """
        logger.info('Starting to find Eulerian circuit in directed graph')
        
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
            edge_data = graph.get_edge_data(u, v, 0)

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
            gpx.simplify()

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
        logger.info(f'Adding track points for segment starting at index {start_index}, interval={arrow_interval}')
        logger.info(f'Number of coordinates to process: {len(coords)}')
        
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
                    logger.info(f'Added direction marker at point {start_index + i}: bearing={bearing}°, coords=({lat}, {lon})')
                
                segment.points.append(point)
            
            logger.info(f'Added {direction_markers_added} direction markers in this segment')
            if direction_markers_added == 0:
                logger.warning('No direction markers were added in this segment')
                logger.warning(f'Segment details: start_index={start_index}, coords={len(coords)}, interval={arrow_interval}')
            
            return direction_markers_added

        except Exception as e:
            logger.error(f"Error adding track points: {str(e)}")
            return None 