from shapely.geometry import LineString, Point
from shapely.ops import unary_union
import math

def decode_polyline(polyline):
    """Decode a Google encoded polyline string into a list of coordinates."""
    coordinates = []
    index = 0
    lat = 0
    lng = 0
    
    while index < len(polyline):
        for coordinate in [lat, lng]:
            shift = 0
            result = 0
            
            while True:
                byte = ord(polyline[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if not byte >= 0x20:
                    break
            
            if result & 1:
                result = ~(result >> 1)
            else:
                result >>= 1
                
            if coordinate == lat:
                lat += result
            else:
                lng += result
                coordinates.append([lat / 100000.0, lng / 100000.0])
    
    return coordinates

def create_activity_map(activities, logger):
    """Create a map of completed streets from activities."""
    activity_lines = []
    
    logger.info(f"Processing {len(activities)} activities")
    activities_processed = 0
    
    for i, activity in enumerate(activities):
        if activity.get('map', {}).get('summary_polyline'):
            coords = decode_polyline(activity['map']['summary_polyline'])
            if coords and len(coords) >= 2:  # Ensure we have at least 2 points
                try:
                    # Fix coordinate order: coords from decode_polyline are [lat, lng]
                    # Convert to [lng, lat] for LineString
                    line_coords = [[lng, lat] for lat, lng in coords]
                    
                    # Validate coordinates
                    valid_coords = []
                    for j, (lng, lat) in enumerate(line_coords):
                        if -180 <= lng <= 180 and -90 <= lat <= 90:
                            valid_coords.append([lng, lat])
                        else:
                            logger.warning(f"Invalid coordinate in activity {i+1}")
                    
                    if len(valid_coords) < 2:
                        logger.warning(f"Not enough valid coordinates for activity {i+1}")
                        continue
                    
                    line = LineString(valid_coords)
                    
                    # Ensure the line is valid
                    if not line.is_valid:
                        line = line.buffer(0).boundary
                        if not line.is_valid:
                            logger.warning(f"Could not fix line geometry for activity {i+1}")
                            continue
                    
                    # Buffer size of 20 meters (roughly 0.0002 degrees)
                    buffered_line = line.buffer(0.0002)
                    
                    if not buffered_line.is_valid:
                        buffered_line = buffered_line.buffer(0)
                        if not buffered_line.is_valid:
                            logger.warning(f"Could not fix buffered geometry for activity {i+1}")
                            continue
                    
                    if buffered_line.is_empty:
                        logger.warning(f"Empty buffered geometry for activity {i+1}")
                        continue
                    
                    activity_lines.append(buffered_line)
                    activities_processed += 1
                    
                    # Log progress every 10 activities
                    if activities_processed % 10 == 0:
                        logger.info(f"Processed {activities_processed}/{len(activities)} activities")
                        
                except Exception as e:
                    logger.warning(f"Error processing activity {i+1}: {str(e)}")
                    continue
    
    if activity_lines:
        logger.info(f"Successfully processed {activities_processed} activities")
        try:
            combined_map = unary_union(activity_lines)
            
            if not combined_map.is_valid:
                combined_map = combined_map.buffer(0)
                if not combined_map.is_valid:
                    logger.error("Could not create valid activity map")
                    return None
            
            if combined_map.is_empty:
                logger.error("Combined map is empty")
                return None
            
            if combined_map.area <= 0:
                logger.error("Combined map has zero or negative area")
                return None
            
            return combined_map
        except Exception as e:
            logger.error(f"Error creating unified activity map: {str(e)}")
            return None
    else:
        logger.warning("No valid activities found to create map")
        return None

def calculate_bearing(lat1, lon1, lat2, lon2):
    """
    Calculate the bearing between two points on the earth.
    Args:
        lat1, lon1: Latitude and longitude of the first point in degrees
        lat2, lon2: Latitude and longitude of the second point in degrees
    Returns:
        Bearing in degrees from 0-360
    """
    # Convert to radians
    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)
    
    # Calculate differences
    d_lon = lon2 - lon1
    
    # Calculate bearing
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    bearing = math.atan2(y, x)
    
    # Convert to degrees
    bearing = math.degrees(bearing)
    
    # Normalize to 0-360
    bearing = (bearing + 360) % 360
    
    return bearing

