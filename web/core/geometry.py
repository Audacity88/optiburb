"""
Geometry Manager Module

This module handles all geometry-related operations for the OptiburB system,
including path creation, direction calculations, and distance measurements.
"""

import math
import shapely.geometry
from web.utils.logging import logger

class GeometryManager:
    def __init__(self):
        """Initialize the GeometryManager."""
        pass

    def create_linestring(self, coords):
        """Create a LineString from coordinates."""
        try:
            linestring = shapely.geometry.LineString(coords)
            if not linestring.is_valid:
                logger.error("Generated LineString is not valid")
                return None
            return linestring
        except Exception as e:
            logger.error(f"Failed to create LineString from coordinates: {str(e)}")
            return None

    def reverse_linestring(self, line):
        """Reverse the direction of a LineString."""
        return shapely.geometry.LineString(line.coords[::-1])

    def calculate_distance(self, point1, point2):
        """Calculate the Euclidean distance between two points."""
        return math.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)

    def calculate_bearing(self, lat1, lon1, lat2, lon2):
        """Calculate the bearing between two points in degrees."""
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        
        # Calculate bearing
        d_lon = lon2 - lon1
        y = math.sin(d_lon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
        bearing = math.atan2(y, x)
        
        # Convert to degrees and normalize to 0-360
        return (math.degrees(bearing) + 360) % 360

    def are_roads_parallel(self, line1, line2, max_distance=0.0003):  # roughly 30 meters
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
                bearing1 = self.calculate_bearing(
                    points1[i].y, points1[i].x,
                    points1[i+1].y, points1[i+1].x
                )
                
                # Calculate bearing for line2
                bearing2 = self.calculate_bearing(
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
            logger.warning(f"Error checking parallel roads: {str(e)}")
            return False

    def get_directional_linestring(self, edge, linestring, node_coords):
        """
        Create a directional linestring for an edge, ensuring the direction matches the edge nodes.
        Returns None if the linestring cannot be created.
        """
        u, v = edge
        try:
            # Get node coordinates
            u_coords = node_coords[u]
            v_coords = node_coords[v]
        except (KeyError, AttributeError) as e:
            logger.error(f"Missing node coordinates for edge {edge}: {str(e)}")
            return None

        try:
            # Get linestring coordinates
            coords = list(linestring.coords)
            if len(coords) < 2:
                logger.error(f"Linestring for edge {edge} has fewer than 2 coordinates")
                return None

            # Get start and end points of the linestring
            start_point = coords[0]
            end_point = coords[-1]

            # Calculate distances to determine if we need to reverse
            start_dist_to_u = self.calculate_distance(start_point, u_coords)
            start_dist_to_v = self.calculate_distance(start_point, v_coords)
            end_dist_to_u = self.calculate_distance(end_point, u_coords)
            end_dist_to_v = self.calculate_distance(end_point, v_coords)

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
                logger.debug(f"Reversing linestring for edge {edge}")
                coords = coords[::-1]

            return coords

        except (AttributeError, IndexError) as e:
            logger.error(f"Error processing linestring for edge {edge}: {str(e)}")
            return None 