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
            if activity.get('map', {}).get('summary_polyline'):
                coords = decode_polyline(activity['map']['summary_polyline'])
                if coords:
                    # Check if any point of the activity is within bounds
                    for lat, lng in coords:
                        if (bounds['minLat'] <= lat <= bounds['maxLat'] and 
                            bounds['minLng'] <= lng <= bounds['maxLng']):
                            filtered_activities.append(activity)
                            break
        
        logger.info(f"Found {len(filtered_activities)} activities in bounds")
        return filtered_activities

    @staticmethod
    def generate_route(location, options, progress_queue, completed_area=None):
        """Generate a route based on the given parameters."""
        try:
            # Initialize Burbing
            burbing = Burbing()
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Starting route generation',
                'progress': 5,
                'message': 'Initializing...'
            }))
            
            # Get polygon and add it
            polygon = burbing.get_osm_polygon(location, select=1, buffer_dist=20)
            burbing.add_polygon(polygon, location)
            
            # Set start location if provided
            if options.start:
                burbing.set_start_location(options.start)
            
            # If we have completed roads to exclude
            if completed_area:
                try:
                    logger.info("Excluding completed roads from route generation")
                    # Store original area for comparison
                    original_area = polygon.area
                    # Subtract completed roads from the polygon
                    polygon = polygon.difference(completed_area)
                    
                    # Check if we haven't excluded too much
                    if polygon.area < (original_area * 0.1):
                        logger.warning("Too much area would be excluded, reverting to original polygon")
                        progress_queue.put(json.dumps({
                            'type': 'progress',
                            'step': 'Processing Strava data',
                            'progress': 15,
                            'message': 'Too many completed roads, using original area'
                        }))
                    else:
                        # Update the polygon in the Burbing instance
                        burbing.polygons = [polygon]
                        burbing.polygon_names = [location]
                        burbing.completed_area = completed_area
                except Exception as e:
                    logger.error(f"Error processing completed roads: {str(e)}")
                    return None, str(e)
            
            # Load and process the graph
            burbing.load(options)
            
            # If we have completed roads to exclude, filter the graph after loading
            if hasattr(burbing, 'completed_area'):
                logger.info("Filtering graph to exclude completed roads")
                edges_to_remove = []
                total_edges = len(burbing.g.edges())
                edges_processed = 0
                
                # Create a buffer around the completed area
                completed_area_buffer = burbing.completed_area.buffer(0.00005)  # ~5 meter buffer
                
                for u, v, data in burbing.g.edges(data=True):
                    edges_processed += 1
                    
                    try:
                        if 'geometry' not in data:
                            continue
                            
                        coords = data['geometry'].coords
                        if len(coords) < 2:
                            continue
                            
                        edge_line = LineString(coords)
                        edge_buffer = edge_line.buffer(0.00002)  # ~2 meter buffer
                        
                        if edge_buffer.intersects(completed_area_buffer):
                            intersection = edge_buffer.intersection(completed_area_buffer)
                            intersection_area = intersection.area if hasattr(intersection, 'area') else 0
                            overlap_ratio = intersection_area / edge_buffer.area
                            
                            if overlap_ratio > 0.4:
                                edges_to_remove.append((u, v))
                    except Exception as e:
                        logger.warning(f"Error checking edge {u}-{v} intersection: {str(e)}")
                        continue
                
                if edges_to_remove:
                    if len(edges_to_remove) < (total_edges * 0.9):
                        burbing.g.remove_edges_from(edges_to_remove)
                        removed_percentage = (len(edges_to_remove) / total_edges) * 100
                        logger.info(f"Removed {len(edges_to_remove)} completed road edges ({removed_percentage:.1f}%) from graph")
                    else:
                        logger.warning(f"Too many edges would be removed ({len(edges_to_remove)} of {total_edges}), keeping original graph")
            
            if options.prune:
                burbing.prune()
            
            burbing.determine_nodes()
            
            if options.feature_deadend:
                burbing.optimise_dead_ends()
            
            burbing.determine_combinations()
            burbing.determine_circuit()
            
            # Format location string
            formatted_location = location.lower().replace(' ', '_').replace(',', '')
            
            # Generate GPX file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            gpx_filename = f'burb_track_{formatted_location}_{timestamp}.gpx'
            
            # Create GPX file with full path
            gpx_filepath = os.path.join(settings.ROOT_DIR, gpx_filename)
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
                logger.info(f"Waiting for GPX file to be created (attempt {attempt}/{max_attempts})")
            
            if not gpx_files:
                logger.error("No GPX files found matching the pattern after waiting")
                raise FileNotFoundError("Generated GPX file not found")
            
            # Sort by creation time and get the most recent
            src_path = os.path.join(settings.ROOT_DIR, 
                                  sorted(gpx_files, 
                                        key=lambda x: os.path.getctime(
                                            os.path.join(settings.ROOT_DIR, x)
                                        ))[-1])
            
            dst_path = os.path.join(settings.UPLOAD_FOLDER, os.path.basename(src_path))
            
            logger.info(f"Moving GPX file from {src_path} to {dst_path}")
            
            # Ensure the UPLOAD_FOLDER exists
            os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
            
            # Try to move the file with retries
            max_move_attempts = 3
            move_attempt = 0
            while move_attempt < max_move_attempts:
                try:
                    shutil.move(src_path, dst_path)
                    logger.info(f"Successfully moved GPX file to {dst_path}")
                    break
                except Exception as e:
                    move_attempt += 1
                    if move_attempt == max_move_attempts:
                        logger.error(f"Failed to move GPX file after {max_move_attempts} attempts: {str(e)}")
                        raise
                    logger.warning(f"Failed to move GPX file (attempt {move_attempt}/{max_move_attempts}): {str(e)}")
                    time.sleep(0.5)
            
            return os.path.basename(src_path), None
            
        except Exception as e:
            logger.error(f"Error in route generation: {str(e)}")
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
