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
                'message': 'Loading road network as directed graph...'
            }))
            
            # Load the graph with options
            burbing.load(options)
            
            # Ensure the graph is directed
            if not hasattr(burbing, 'g') or burbing.g is None:
                logger.error("Graph was not created during load")
                return None, "Failed to create graph"
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Processing graph',
                'progress': 40,
                'message': 'Processing directed road network...'
            }))
            
            if options.prune:
                burbing.prune()
            
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
            
            if options.feature_deadend:
                try:
                    burbing.optimise_dead_ends()
                except Exception as e:
                    logger.error(f"Error optimizing dead ends: {str(e)}")
                    # Continue even if dead end optimization fails
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Finding circuit',
                'progress': 80,
                'message': 'Finding Eulerian circuit in directed graph...'
            }))
            
            # Find Eulerian circuit in directed graph
            try:
                burbing.determine_combinations()
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
            
            formatted_location = location.lower().replace(' ', '_').replace(',', '')
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            gpx_filename = f'burb_track_{formatted_location}_{timestamp}.gpx'
            
            # Create GPX track from directed graph
            try:
                burbing.create_gpx_track(burbing.g_augmented, burbing.euler_circuit, options.simplify_gpx)
            except Exception as e:
                logger.error(f"Error creating GPX track: {str(e)}")
                return None, f"Error creating GPX file: {str(e)}"
            
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
            logger.error(f"Error in route generation: {str(e)}")
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
            
            # Convert to GeoJSON with direction indicators
            features = []
            direction_count = 0
            
            for track in gpx.tracks:
                for segment in track.segments:
                    coordinates = []
                    direction_points = []
                    
                    # Store all coordinates first
                    for i, point in enumerate(segment.points):
                        coordinates.append([point.longitude, point.latitude])
                    
                    # Add direction markers at edge midpoints
                    if len(coordinates) >= 2:
                        direction_points = []
                        
                        # Process each edge (pair of consecutive points)
                        for i in range(len(coordinates) - 1):
                            # Get current and next point
                            p1 = coordinates[i]
                            p2 = coordinates[i + 1]
                            
                            # Calculate point 30% along the edge
                            point = [
                                p1[0] + (p2[0] - p1[0]) * 0.7,  # longitude
                                p1[1] + (p2[1] - p1[1]) * 0.7   # latitude
                            ]
                            
                            # Calculate bearing between the two points
                            bearing = calculate_bearing(
                                p1[1], p1[0],  # lat, lon of start
                                p2[1], p2[0]   # lat, lon of end
                            )
                            
                            direction_points.append({
                                'index': i,
                                'coordinates': point,
                                'bearing': bearing
                            })
                            direction_count += 1
                            logger.info(f"Added direction marker at edge {i} midpoint with bearing {bearing}°")
                    
                    if not coordinates:
                        logger.warning(f"No coordinates found in track segment")
                        continue
                    
                    logger.info(f"Processing track segment with {len(coordinates)} points and {len(direction_points)} direction markers")
                    
                    # Create main route feature
                    route_feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": coordinates
                        },
                        "properties": {
                            "name": track.name or "Route",
                            "type": "route"
                        }
                    }
                    features.append(route_feature)
                    
                    # Create features for direction markers
                    for direction in direction_points:
                        marker_feature = {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": direction['coordinates']
                            },
                            "properties": {
                                "type": "direction",
                                "bearing": direction['bearing']
                            }
                        }
                        features.append(marker_feature)
                        logger.debug(f"Added direction marker feature with bearing {direction['bearing']}°")
            
            logger.info(f"Total direction markers found: {direction_count}")
            
            if not features:
                logger.error("No valid features found in GPX file")
                return None, "No valid route data found"
            
            geojson = {
                "type": "FeatureCollection",
                "features": features
            }
            
            # Calculate bounds
            route_features = [f for f in features if f["properties"]["type"] == "route"]
            direction_features = [f for f in features if f["properties"]["type"] == "direction"]
            
            logger.info(f"GeoJSON contains {len(route_features)} route features and {len(direction_features)} direction features")
            
            # Calculate bounds from route features
            if route_features:
                all_coords = []
                for feature in route_features:
                    coords = feature["geometry"]["coordinates"]
                    all_coords.extend(coords)
                
                min_lng = min(coord[0] for coord in all_coords)
                max_lng = max(coord[0] for coord in all_coords)
                min_lat = min(coord[1] for coord in all_coords)
                max_lat = max(coord[1] for coord in all_coords)
                
                bounds = {
                    "minLat": min_lat,
                    "maxLat": max_lat,
                    "minLng": min_lng,
                    "maxLng": max_lng
                }
            else:
                bounds = None
            
            return {
                "geojson": geojson,
                "bounds": bounds
            }, None
            
        except Exception as e:
            logger.error(f"Error processing GPX file: {str(e)}")
            return None, str(e)
