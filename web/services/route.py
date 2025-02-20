import os
import gpxpy
import json
from datetime import datetime
import shutil
from web.utils.logging import logger
from web.config import settings
from web.utils.geometry import create_activity_map, decode_polyline, calculate_bearing
from optiburb import Burbing
from shapely.geometry import LineString
import time
import shapely

class RouteService:
    @staticmethod
    def get_user_activities(access_token, bounds, activities):
        """Get user activities within the given bounds."""
        if not activities:
            logger.warning("No activities provided")
            return None
        
        logger.info(f"Using {len(activities)} cached activities")
        
        # Filter activities by bounds
        filtered_activities = []
        logger.info(f"Filtering {len(activities)} activities for bounds: {bounds}")
        
        for activity in activities:
            polyline = activity.get('map', {}).get('summary_polyline')
            if not polyline:
                continue
                
            coords = decode_polyline(polyline)
            if not coords:
                continue
                
            # Quick check using bounding box of activity
            activity_lats = [lat for lat, _ in coords]
            activity_lngs = [lng for _, lng in coords]
            
            # If activity's bounding box doesn't overlap with target bounds, skip it
            if (min(activity_lats) > bounds['maxLat'] or 
                max(activity_lats) < bounds['minLat'] or 
                min(activity_lngs) > bounds['maxLng'] or 
                max(activity_lngs) < bounds['minLng']):
                continue
            
            # If we get here, the activity overlaps with our target area
            filtered_activities.append(activity)
        
        logger.info(f"Found {len(filtered_activities)} activities in bounds")
        return filtered_activities

    @staticmethod
    def generate_route(location, options, progress_queue, completed_area=None, existing_burbing=None):
        """Generate a route based on the given parameters."""
        try:
            # Initialize or use existing Burbing instance
            burbing = existing_burbing if existing_burbing else Burbing()
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Starting route generation',
                'progress': 5,
                'message': 'Initializing route generator...'
            }))
            
            # Only get polygon and add it if we don't have an existing instance
            if not existing_burbing:
                polygon = burbing.data_loader.load_osm_data(location, select=1, buffer_dist=20)
                if not polygon:
                    progress_queue.put(json.dumps({
                        'type': 'progress',
                        'message': 'Failed to get OSM polygon'
                    }))
                    return None, "Failed to get OSM polygon"
                
                burbing.add_polygon(polygon, location)
                
                # Validate polygon was added correctly
                if not hasattr(burbing, 'polygons') or not burbing.polygons:
                    progress_queue.put(json.dumps({
                        'type': 'progress',
                        'message': 'Failed to initialize area polygon'
                    }))
                    return None, "Failed to initialize polygons"
                
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'step': 'Area defined',
                    'progress': 15,
                    'message': 'Successfully defined target area'
                }))
            
            # Set start location if provided
            if options.start:
                burbing.set_start_location(options.start)
            
            # If we have completed roads to exclude
            if completed_area:
                try:
                    if not completed_area.is_valid:
                        completed_area = completed_area.buffer(0)
                    
                    if not completed_area.is_empty:
                        burbing.completed_area = completed_area
                        progress_queue.put(json.dumps({
                            'type': 'progress',
                            'step': 'Processing Strava data',
                            'progress': 20,
                            'message': 'Loaded completed roads data'
                        }))
                except Exception as e:
                    logger.error(f"Error processing completed roads: {str(e)}")
            
            # Load and process the graph
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Loading map data',
                'progress': 25,
                'message': 'Loading road network as directed graph...'
            }))
            
            # Load the graph with options
            burbing.load(options)
            
            # Ensure the graph is directed
            if not hasattr(burbing.graph_manager, 'g') or burbing.graph_manager.g is None:
                logger.error("Graph was not created during load")
                return None, "Failed to create graph"
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Processing graph',
                'progress': 40,
                'message': 'Processing directed road network...'
            }))
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Balancing graph',
                'progress': 60,
                'message': 'Balancing directed graph...'
            }))
            
            # Process nodes and balance the graph
            try:
                burbing.determine_nodes()
            except Exception as e:
                logger.error(f"Error determining nodes: {str(e)}")
                return None, f"Error processing graph nodes: {str(e)}"
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Finding circuit',
                'progress': 80,
                'message': 'Finding Eulerian circuit in directed graph...'
            }))
            
            # Find Eulerian circuit in directed graph
            try:
                burbing.determine_circuit()
            except ValueError as e:
                logger.error(f"Error finding Eulerian circuit: {str(e)}")
                return None, f"Error finding Eulerian circuit: {str(e)}"
            except Exception as e:
                logger.error(f"Unexpected error in circuit determination: {str(e)}")
                return None, f"Error in route calculation: {str(e)}"
            
            # Generate GPX file
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Creating GPX',
                'progress': 90,
                'message': 'Generating GPX file from directed route...'
            }))
            
            # Create GPX track from directed graph
            try:
                gpx_filename = burbing.create_gpx_track(options.simplify_gpx)
                
                # Verify the file exists
                gpx_filepath = os.path.join(settings.UPLOAD_FOLDER, gpx_filename)
                if not os.path.exists(gpx_filepath):
                    raise FileNotFoundError(f"Generated GPX file not found at {gpx_filepath}")
                
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'step': 'Complete',
                    'progress': 100,
                    'message': 'Route generated successfully!'
                }))
                
                return gpx_filename, None
                
            except Exception as e:
                logger.error(f"Error creating GPX track: {str(e)}")
                return None, f"Error creating GPX file: {str(e)}"
            
        except Exception as e:
            logger.error(f"Error in route generation: {str(e)}")
            progress_queue.put(json.dumps({
                'type': 'progress',
                'message': f'Error: {str(e)}'
            }))
            return None, str(e)

    @staticmethod
    def get_route_data(filename):
        """Get route data from a GPX file."""
        try:
            file_path = os.path.join(settings.UPLOAD_FOLDER, filename)
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                return None, "File not found"
            
            # Parse GPX file
            with open(file_path, 'r') as gpx_file:
                gpx = gpxpy.parse(gpx_file)
            
            # Extract route points and direction markers
            features = []
            bounds = {
                "minLat": float('inf'),
                "maxLat": float('-inf'),
                "minLng": float('inf'),
                "maxLng": float('-inf')
            }
            
            for track in gpx.tracks:
                for segment in track.segments:
                    route_coords = []
                    prev_point = None
                    is_straight_line = False
                    
                    for point in segment.points:
                        # Update bounds
                        bounds["minLat"] = min(bounds["minLat"], point.latitude)
                        bounds["maxLat"] = max(bounds["maxLat"], point.latitude)
                        bounds["minLng"] = min(bounds["minLng"], point.longitude)
                        bounds["maxLng"] = max(bounds["maxLng"], point.longitude)
                        
                        # Add point to route coordinates
                        route_coords.append([point.longitude, point.latitude])
                        
                        # Check if this is a straight line segment
                        if hasattr(point, 'type') and point.type == 'straight_line':
                            is_straight_line = True
                        
                        # Check if this is a direction marker
                        if hasattr(point, 'type') and point.type == 'direction' and prev_point:
                            # Calculate bearing if not provided
                            bearing = float(point.comment) if hasattr(point, 'comment') else calculate_bearing(
                                prev_point.latitude, prev_point.longitude,
                                point.latitude, point.longitude
                            )
                            
                            # Add direction marker feature
                            features.append({
                                "type": "Feature",
                                "geometry": {
                                    "type": "Point",
                                    "coordinates": [point.longitude, point.latitude]
                                },
                                "properties": {
                                    "type": "direction",
                                    "bearing": bearing
                                }
                            })
                        
                        prev_point = point
                    
                    # Add route line feature
                    if route_coords:
                        features.append({
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": route_coords
                            },
                            "properties": {
                                "type": "straight_line" if is_straight_line else "route"
                            }
                        })
            
            return {
                "geojson": {
                    "type": "FeatureCollection",
                    "features": features
                },
                "bounds": bounds
            }, None
            
        except Exception as e:
            logger.error(f"Error processing route data: {str(e)}")
            return None, str(e)
