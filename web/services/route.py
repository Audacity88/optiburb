import os
import gpxpy
import json
from datetime import datetime
import shutil
from web.utils.logging import logger
from web.config import settings
from web.utils.geometry import create_activity_map, decode_polyline
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
                polygon = burbing.get_osm_polygon(location, select=1, buffer_dist=20)
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
                'message': 'Loading road network...'
            }))
            
            burbing.load(options)
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Calculating route',
                'progress': 60,
                'message': 'Determining optimal route...'
            }))
            
            if options.prune:
                burbing.prune()
            
            burbing.determine_nodes()
            
            if options.feature_deadend:
                burbing.optimise_dead_ends()
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Finalizing route',
                'progress': 80,
                'message': 'Calculating final path...'
            }))
            
            burbing.determine_combinations()
            burbing.determine_circuit()
            
            # Generate GPX file
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Creating GPX',
                'progress': 90,
                'message': 'Generating GPX file...'
            }))
            
            formatted_location = location.lower().replace(' ', '_').replace(',', '')
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            gpx_filename = f'burb_track_{formatted_location}_{timestamp}.gpx'
            
            burbing.create_gpx_track(burbing.g_augmented, burbing.euler_circuit, options.simplify_gpx)
            
            # Wait for the file to be created (up to 5 seconds)
            max_attempts = 10
            attempt = 0
            while attempt < max_attempts:
                gpx_files = [f for f in os.listdir(settings.ROOT_DIR) 
                            if f.startswith(f'burb_track_{formatted_location}_') and f.endswith('.gpx')]
                if gpx_files:
                    break
                time.sleep(0.5)
                attempt += 1
            
            if not gpx_files:
                raise FileNotFoundError("Generated GPX file not found")
            
            # Sort by creation time and get the most recent
            src_path = os.path.join(settings.ROOT_DIR, 
                                  sorted(gpx_files, 
                                        key=lambda x: os.path.getctime(
                                            os.path.join(settings.ROOT_DIR, x)
                                        ))[-1])
            
            dst_path = os.path.join(settings.UPLOAD_FOLDER, os.path.basename(src_path))
            
            # Ensure the UPLOAD_FOLDER exists
            os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
            
            # Try to move the file with retries
            max_move_attempts = 3
            move_attempt = 0
            while move_attempt < max_move_attempts:
                try:
                    shutil.move(src_path, dst_path)
                    break
                except Exception as e:
                    move_attempt += 1
                    if move_attempt == max_move_attempts:
                        raise
                    time.sleep(0.5)
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Complete',
                'progress': 100,
                'message': 'Route generated successfully!'
            }))
            
            return os.path.basename(src_path), None
            
        except Exception as e:
            progress_queue.put(json.dumps({
                'type': 'progress',
                'message': f'Error in route generation: {str(e)}'
            }))
            return None, str(e)

    @staticmethod
    def get_route_data(filename):
        """Get route data from a GPX file."""
        try:
            file_path = os.path.join(settings.UPLOAD_FOLDER, filename)
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                raise FileNotFoundError(f"File not found: {filename}")
            
            logger.info(f"Reading GPX file: {file_path}")
            # Parse GPX file
            with open(file_path, 'r') as gpx_file:
                gpx = gpxpy.parse(gpx_file)
            
            # Convert to GeoJSON
            features = []
            for track in gpx.tracks:
                for segment in track.segments:
                    coordinates = [[point.longitude, point.latitude] for point in segment.points]
                    if not coordinates:
                        logger.warning(f"No coordinates found in track segment")
                        continue
                        
                    logger.info(f"Found {len(coordinates)} points in track segment")
                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": coordinates
                        },
                        "properties": {
                            "name": track.name or "Route"
                        }
                    }
                    features.append(feature)
            
            if not features:
                logger.error("No valid features found in GPX file")
                return None, "No valid route data found"
            
            geojson = {
                "type": "FeatureCollection",
                "features": features
            }
            
            # Calculate bounds
            if features and features[0]["geometry"]["coordinates"]:
                coords = features[0]["geometry"]["coordinates"]
                bounds = {
                    "minLat": min(c[1] for c in coords),
                    "maxLat": max(c[1] for c in coords),
                    "minLng": min(c[0] for c in coords),
                    "maxLng": max(c[0] for c in coords)
                }
                logger.info(f"Calculated bounds: {bounds}")
            else:
                logger.error("No coordinates found to calculate bounds")
                bounds = None
            
            return {
                "geojson": geojson,
                "bounds": bounds
            }, None
            
        except Exception as e:
            logger.error(f"Error getting route data: {str(e)}")
            return None, str(e)
