import os
import gpxpy
import shapely.geometry
from web.utils.logging import logger
from web.config import settings

def _is_coincident_with_straight_line(self, segment_coords, straight_line_segments, buffer_distance=0.0003):
    """Check if a segment is coincident with any straight line segments within buffer distance (~30m)"""
    segment = shapely.geometry.LineString(segment_coords)
    segment_buffer = segment.buffer(buffer_distance)
    
    for straight_segment in straight_line_segments:
        straight_line = shapely.geometry.LineString(straight_segment['coordinates'])
        if segment_buffer.intersects(straight_line):
            # Check if most of the line overlaps (>80% overlap)
            intersection = segment_buffer.intersection(straight_line)
            overlap_ratio = intersection.length / straight_line.length
            if overlap_ratio > 0.8:
                return True
    return False

def get_route_data(self, gpx_file):
        """
        Get route data including completion status from a GPX file.
        
        Args:
            gpx_file (str): Name of the GPX file
            
        Returns:
            dict: Route data including bounds, features, and completion status
        """
        logger.info(f'Starting route completion calculation for: {gpx_file}')
        
        # Parse GPX file
        gpx_path = os.path.join(settings.UPLOAD_FOLDER, gpx_file)
        if not os.path.exists(gpx_path):
            raise FileNotFoundError(f'GPX file not found: {gpx_file}')
        
        try:
            with open(gpx_path, 'r') as f:
                gpx = gpxpy.parse(f)
            logger.info('Successfully parsed GPX file')
        except Exception as e:
            logger.error(f'Error parsing GPX file: {str(e)}')
            raise
        
        # Extract bounds and features
        bounds = {
            'minLat': float('inf'),
            'maxLat': float('-inf'),
            'minLng': float('inf'),
            'maxLng': float('-inf')
        }
        
        features = []
        points = []
        
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    # Update bounds
                    bounds['minLat'] = min(bounds['minLat'], point.latitude)
                    bounds['maxLat'] = max(bounds['maxLat'], point.latitude)
                    bounds['minLng'] = min(bounds['minLng'], point.longitude)
                    bounds['maxLng'] = max(bounds['maxLng'], point.longitude)
                    
                    # Create point feature
                    point_type = getattr(point, 'type', 'route')
                    point_feature = {
                        'type': 'Feature',
                        'geometry': {
                            'type': 'Point',
                            'coordinates': [point.longitude, point.latitude]
                        },
                        'properties': {
                            'type': point_type
                        }
                    }
                    
                    # Add bearing for direction points
                    if point_type == 'direction' and hasattr(point, 'comment'):
                        point_feature['properties']['bearing'] = float(point.comment)
                    
                    features.append(point_feature)
                    points.append(point)
        
        logger.info(f'Route bounds: {bounds}')
        
        # Get activities in the area
        activities = self.activity_service.get_activities_in_bounds(bounds)
        logger.info(f'Found {len(activities)} activities in the area')
        
        # Process activities for display
        processed_activities = []
        for activity in activities:
            try:
                processed = self.activity_service.process_activity_for_display(activity)
                if processed:
                    processed_activities.append(processed)
            except Exception as e:
                logger.error(f'Error processing activity {activity.get("id")}: {str(e)}')
        
        logger.info(f'Processed {len(processed_activities)} activities for display')
        
        # Create activity map
        logger.info('Creating activity map from processed activities...')
        activity_map = self.activity_service.create_activity_map(processed_activities)
        if not activity_map:
            logger.warning('No activity map created')
            activity_map = shapely.geometry.Polygon([])
        logger.info('Successfully created activity map')
        
        # Process route segments
        logger.info('Starting route segment processing...')
        segments = []
        straight_line_segments = []
        total_distance = 0
        completed_distance = 0
        
        # Group points into segments
        current_segment = []
        is_straight_line = False
        
        for i, point in enumerate(points):
            point_type = getattr(point, 'type', 'route')
            
            # Start new segment if point type changes
            if current_segment and (
                (point_type == 'straight_line' and not is_straight_line) or
                (point_type != 'straight_line' and is_straight_line) or
                point_type == 'direction'
            ):
                if len(current_segment) >= 2:
                    segment_coords = [(p.longitude, p.latitude) for p in current_segment]
                    segment_line = shapely.geometry.LineString(segment_coords)
                    segment_length = self.geometry.calculate_length(segment_line)
                    total_distance += segment_length
                    
                    segment_data = {
                        'coordinates': segment_coords,
                        'is_completed': False,
                        'is_straight_line': is_straight_line,
                        'length': segment_length
                    }
                    
                    if is_straight_line:
                        straight_line_segments.append(segment_data)
                    else:
                        # Check if this route segment is coincident with any straight line
                        if self._is_coincident_with_straight_line(segment_coords, straight_line_segments):
                            segment_data['is_straight_line'] = True
                            logger.info(f'Found route segment coincident with straight line at {segment_coords[0]}')
                    
                    # Check completion for non-straight line segments
                    if not segment_data['is_straight_line']:
                        segment_buffer = segment_line.buffer(0.0001)  # ~10m buffer
                        segment_data['is_completed'] = segment_buffer.intersects(activity_map)
                        if segment_data['is_completed']:
                            completed_distance += segment_length
                    
                    segments.append(segment_data)
                
                current_segment = []
                is_straight_line = point_type == 'straight_line'
            
            # Skip direction points
            if point_type != 'direction':
                current_segment.append(point)
                is_straight_line = point_type == 'straight_line'
        
        # Split segments into completed and incomplete
        completed_segments = [s for s in segments if s['is_completed']]
        incomplete_segments = [s for s in segments if not s['is_completed']]
        
        # Log processing summary
        logger.info('Route processing summary:')
        logger.info(f'Total segments: {len(segments)}')
        logger.info(f'Completed segments: {len(completed_segments)}')
        logger.info(f'Total distance: {total_distance/1000:.2f}km')
        logger.info(f'Completed distance: {completed_distance/1000:.2f}km')
        logger.info(f'Total completion: {(completed_distance/total_distance*100):.2f}%')
        
        # Return route data
        return {
            'bounds': bounds,
            'geojson': {
                'type': 'FeatureCollection',
                'features': features
            },
            'activities': processed_activities,
            'completed_segments': completed_segments,
            'incomplete_segments': incomplete_segments,
            'total_distance': total_distance,
            'completed_distance': completed_distance,
            'total_completion': completed_distance/total_distance if total_distance > 0 else 0
        } 