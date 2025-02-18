from shapely.geometry import LineString, Point
from shapely.ops import unary_union

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
    
    logger.info(f"Creating activity map from {len(activities)} activities")
    for i, activity in enumerate(activities):
        if activity.get('map', {}).get('summary_polyline'):
            coords = decode_polyline(activity['map']['summary_polyline'])
            if coords:
                try:
                    # Fix coordinate order: coords from decode_polyline are [lat, lng]
                    # Convert to [lng, lat] for LineString
                    line_coords = [[lng, lat] for lat, lng in coords]
                    line = LineString(line_coords)
                    # Buffer size of 10 meters (roughly 0.0001 degrees)
                    buffered_line = line.buffer(0.0001)
                    activity_lines.append(buffered_line)
                    logger.debug(f"Added activity {i+1} to map with {len(coords)} points")
                except Exception as e:
                    logger.warning(f"Error processing activity {i+1}: {str(e)}")
                    continue
    
    if activity_lines:
        logger.info(f"Successfully processed {len(activity_lines)} activities for the map")
        try:
            combined_map = unary_union(activity_lines)
            return combined_map
        except Exception as e:
            logger.error(f"Error creating unified activity map: {str(e)}")
            return None
    else:
        logger.warning("No valid activities found to create map")
        return None

