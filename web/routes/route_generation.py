from flask import Blueprint, jsonify, request, send_file, Response, session, redirect, url_for
import queue
import argparse
import json
from functools import wraps
from web.utils.logging import logger, ProgressHandler
from web.services.strava import StravaService
from web.services.route import RouteService
from web.config import settings
from web.utils.geometry import create_activity_map, decode_polyline
import os
from shapely.geometry import LineString
import gpxpy
from optiburb import Burbing
import datetime
from optiburb import Burbing
import shapely.geometry
from web.services.route_analysis import RouteAnalysisService

routes = Blueprint('routes', __name__)

# Progress queues for each session
progress_queues = {}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'strava_token' not in session:
            return redirect(url_for('auth.strava_login'))
        return f(*args, **kwargs)
    return decorated_function

@routes.route('/progress/<session_id>')
def progress(session_id):
    def generate():
        if session_id not in progress_queues:
            return
        
        q = progress_queues[session_id]
        try:
            while True:
                message = q.get(timeout=60)  # 1 minute timeout
                yield f"data: {message}\n\n"
        except queue.Empty:
            progress_queues.pop(session_id, None)
            yield "data: {\"type\": \"done\"}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@routes.route('/generate', methods=['POST'])
def generate_route():
    progress_handler = None
    try:
        data = request.get_json()
        location = data.get('location')
        start_point = data.get('start_point')
        center_coordinates = data.get('center_coordinates')
        session_id = data.get('session_id')
        
        # Create progress queue for this session
        progress_queue = queue.Queue()
        progress_queues[session_id] = progress_queue
        
        # Create progress handler and store it for cleanup
        progress_handler = ProgressHandler(progress_queue)
        
        # Remove any existing handlers of the same type to prevent duplicates
        for handler in logger.handlers[:]:
            if isinstance(handler, ProgressHandler):
                logger.removeHandler(handler)
        
        # Add the new handler
        logger.addHandler(progress_handler)

        # Initialize Burbing with buffer size
        buffer_meters = data.get('buffer', 500)  # Default to 500m if not provided
        buffer_degrees = buffer_meters / 111000  # Convert meters to degrees (approximately)
        logger.info(f"Using buffer size: {buffer_meters}m ({buffer_degrees:.6f} degrees)")
        
        burbing = Burbing()
        existing_burbing = hasattr(burbing, 'polygons') and burbing.polygons
        
        # Only get polygon and add it if we don't have an existing instance
        if not existing_burbing:
            # Create a point from the center coordinates and buffer it
            if not center_coordinates:
                progress_queue.put(json.dumps({
                    'type': 'error',
                    'message': 'Center coordinates are required'
                }))
                return jsonify({'error': 'Center coordinates are required'}), 400
                
            point = shapely.geometry.Point(center_coordinates[1], center_coordinates[0])  # lon, lat order
            polygon = point.buffer(buffer_degrees)
            
            if not polygon:
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'message': 'Failed to create buffer polygon'
                }))
                return jsonify({'error': 'Failed to create buffer polygon'}), 400
            
            burbing.add_polygon(polygon, location)
            
            # Validate polygon was added correctly
            if not hasattr(burbing, 'polygons') or not burbing.polygons:
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'message': 'Failed to initialize area polygon'
                }))
                return jsonify({'error': 'Failed to initialize polygons'}), 400
            
            # Get the center coordinates of the polygon
            center_lat, center_lng = polygon.centroid.y, polygon.centroid.x
            
            progress_queue.put(json.dumps({
                'type': 'progress',
                'step': 'Area defined',
                'progress': 15,
                'message': 'Successfully defined target area',
                'coordinates': [center_lat, center_lng]
            }))
        
        # Get bounds from the polygon
        minx, miny, maxx, maxy = polygon.bounds
        logger.info(f"Area bounds: minLat={miny}, maxLat={maxy}, minLng={minx}, maxLng={maxx}")

        # Set start location if provided
        if start_point:
            try:
                burbing.set_start_location(start_point)
            except ValueError as e:
                progress_queue.put(json.dumps({
                    'type': 'error',
                    'message': str(e)
                }))
                return jsonify({'error': str(e)}), 400

        # Check for Strava authentication and get completed area if needed
        completed_area = None
        if data.get('exclude_completed', False) and 'strava_token' in session:
            logger.info("Exclude completed roads option is enabled")
            access_token = session['strava_token']['access_token']
            
            # Load and filter activities within bounds
            activities = StravaService.load_activities_from_disk(access_token)[0]
            if activities:
                logger.info(f"Found {len(activities)} total activities in cache")
                filtered_activities = RouteService.get_user_activities(access_token, {
                    'minLat': miny,
                    'minLng': minx,
                    'maxLat': maxy,
                    'maxLng': maxx
                }, activities)
                
                if filtered_activities:
                    logger.info(f"Found {len(filtered_activities)} activities in the target area")
                    completed_area = create_activity_map(filtered_activities, logger)
                    if completed_area:
                        logger.info(f"Successfully created completed area: valid={completed_area.is_valid}, empty={completed_area.is_empty}, area={completed_area.area}")
                    else:
                        logger.warning("Failed to create completed area from activities")
                    progress_queue.put(json.dumps({
                        'type': 'progress',
                        'step': 'Processing Strava data',
                        'progress': 10,
                        'message': f'Found {len(filtered_activities)} activities in the area'
                    }))
                else:
                    logger.warning("No activities found in the target area")
            else:
                logger.warning("No activities found in cache")

        # Convert dictionary to argparse.Namespace
        options = argparse.Namespace(
            simplify=data.get('simplify', False),
            prune=data.get('prune', False),
            simplify_gpx=data.get('simplify_gpx', True),
            exclude_completed=data.get('exclude_completed', False),
            debug='info',
            start=start_point,
            names=[location],
            select=1,
            buffer=buffer_degrees if 'buffer_degrees' in locals() else data.get('buffer', 500) / 111000,
            shapefile=None,
            save_fig=False,
            save_boundary=False,
            complex_gpx=not data.get('simplify_gpx', True)
        )

        # Generate route
        gpx_filename, error = RouteService.generate_route(location, options, progress_queue, completed_area, burbing)
        if error:
            return jsonify({'error': error}), 500

        # Generate AI summary
        strava_token = session.get('strava_token', {}).get('access_token') if 'strava_token' in session else None
        route_summary = RouteAnalysisService.analyze_route(gpx_filename, strava_token)

        progress_queue.put(json.dumps({
            'type': 'progress',
            'step': 'Route generation complete',
            'progress': 100,
            'message': 'Route generated successfully!'
        }))
        
        return jsonify({
            'success': True,
            'message': 'Route generated successfully',
            'gpx_file': gpx_filename,
            'summary': route_summary
        })
        
    except Exception as e:
        logger.error(f"Error generating route: {str(e)}")
        if session_id in progress_queues:
            progress_queues[session_id].put(json.dumps({
                'type': 'error',
                'message': str(e)
            }))
        return jsonify({'error': str(e)}), 500
    
    finally:
        # Clean up the progress handler in all cases
        if progress_handler and progress_handler in logger.handlers:
            logger.removeHandler(progress_handler)
        # Clean up the progress queue
        if 'session_id' in locals() and session_id in progress_queues:
            progress_queues.pop(session_id, None)

@routes.route('/download/<filename>')
def download_file(filename):
    try:
        file_path = os.path.join(settings.UPLOAD_FOLDER, filename)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {filename}")
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return jsonify({'error': 'File not found'}), 404

@routes.route('/route/<filename>')
def get_route(filename):
    """Get route data from a GPX file."""
    try:
        data, error = RouteService.get_route_data(filename)
        if error:
            return jsonify({'error': error}), 500
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error processing route data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@routes.route('/route/<filename>/completion')
@login_required
def get_route_completion(filename):
    try:
        file_path = os.path.join(settings.UPLOAD_FOLDER, filename)
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            raise FileNotFoundError(f"File not found: {filename}")
        
        logger.info(f"Starting route completion calculation for: {filename}")
        
        # Parse GPX file
        with open(file_path, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            logger.info("Successfully parsed GPX file")
        
        # Get route bounds
        bounds = {
            "minLat": min(point.latitude for track in gpx.tracks for segment in track.segments for point in segment.points),
            "maxLat": max(point.latitude for track in gpx.tracks for segment in track.segments for point in segment.points),
            "minLng": min(point.longitude for track in gpx.tracks for segment in track.segments for point in segment.points),
            "maxLng": max(point.longitude for track in gpx.tracks for segment in track.segments for point in segment.points)
        }
        logger.info(f"Route bounds: {bounds}")
        
        # Get user's activities in the area
        access_token = session['strava_token']['access_token']
        activities = StravaService.load_activities_from_disk(access_token)[0]
        if not activities:
            return jsonify({
                "completed_segments": [],
                "incomplete_segments": [],
                "total_completion": 0,
                "total_distance": 0,
                "completed_distance": 0,
                "activities": []
            })
        
        filtered_activities = RouteService.get_user_activities(access_token, bounds, activities)
        if not filtered_activities:
            return jsonify({
                "completed_segments": [],
                "incomplete_segments": [],
                "total_completion": 0,
                "total_distance": 0,
                "completed_distance": 0,
                "activities": []
            })
        
        logger.info(f"Found {len(filtered_activities)} activities in the area")
        
        # Process activities for display
        activity_features = []
        for i, activity in enumerate(filtered_activities):
            if activity.get('map', {}).get('summary_polyline'):
                coords = decode_polyline(activity['map']['summary_polyline'])
                if coords:
                    logger.debug(f"Processing activity {i+1}: {activity.get('name')} with {len(coords)} coordinates")
                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[lng, lat] for lat, lng in coords]
                        },
                        "properties": {
                            "name": activity.get('name', 'Unnamed Activity'),
                            "distance": activity.get('distance', 0),
                            "date": activity.get('start_date_local', ''),
                            "type": activity.get('type', 'Unknown'),
                            "id": str(activity.get('id', ''))
                        }
                    }
                    activity_features.append(feature)
        
        logger.info(f"Processed {len(activity_features)} activities for display")
        
        # Create activity map
        logger.info("Creating activity map from processed activities...")
        activity_map = create_activity_map(filtered_activities, logger)
        
        if not activity_map:
            logger.warning("Could not create activity map from activities")
            return jsonify({
                "completed_segments": [],
                "incomplete_segments": [],
                "total_completion": 0,
                "total_distance": 0,
                "completed_distance": 0,
                "activities": activity_features
            })
        
        logger.info("Successfully created activity map")
        
        # Process route segments and check completion
        completed_segments = []
        incomplete_segments = []
        total_distance = 0
        completed_distance = 0
        segments_processed = 0
        total_segments = sum(len(segment.points) - 1 for track in gpx.tracks for segment in track.segments)
        
        logger.info("Starting route segment processing...")
        
        # Create a buffer around the activity map (5 meters)
        activity_buffer = activity_map.buffer(0.00005)
        
        # Break the route into smaller segments
        for track in gpx.tracks:
            for segment in track.segments:
                points = segment.points
                for i in range(len(points) - 1):
                    start_point = points[i]
                    end_point = points[i + 1]
                    
                    # Check if either point is a straight line point
                    is_straight_line = (hasattr(start_point, 'type') and start_point.type == 'straight_line') or \
                                     (hasattr(end_point, 'type') and end_point.type == 'straight_line')
                    
                    coords = [
                        [start_point.longitude, start_point.latitude],
                        [end_point.longitude, end_point.latitude]
                    ]
                    
                    route_segment = LineString(coords)
                    segment_length = route_segment.length
                    total_distance += segment_length
                    
                    # For straight line segments, add them to incomplete segments with the straight line flag
                    if is_straight_line:
                        incomplete_segments.append({
                            "coordinates": coords,
                            "completion": 0.0,
                            "is_straight_line": True
                        })
                        continue
                    
                    try:
                        # Buffer the route segment (2 meters)
                        route_buffer = route_segment.buffer(0.00002)
                        
                        # Check intersection with activity map
                        if route_buffer.intersects(activity_buffer):
                            intersection = route_buffer.intersection(activity_buffer)
                            intersection_area = intersection.area if hasattr(intersection, 'area') else 0
                            overlap_ratio = intersection_area / route_buffer.area
                            
                            if overlap_ratio > 0.7:  # Consider segment completed if 70% overlaps
                                completed_segments.append({
                                    "coordinates": coords,
                                    "completion": 1.0,
                                    "is_straight_line": False
                                })
                                completed_distance += segment_length
                            else:
                                incomplete_segments.append({
                                    "coordinates": coords,
                                    "completion": overlap_ratio,
                                    "is_straight_line": False
                                })
                                
                        else:
                            incomplete_segments.append({
                                "coordinates": coords,
                                "completion": 0.0,
                                "is_straight_line": False
                            })
                    except Exception as e:
                        logger.error(f"Error processing segment: {str(e)}")
                        incomplete_segments.append({
                            "coordinates": coords,
                            "completion": 0.0,
                            "is_straight_line": False
                        })
                    
                    segments_processed += 1
                    if segments_processed % 100 == 0:  # Log progress every 100 segments
                        logger.info(f"Processed {segments_processed}/{total_segments} segments")
        
        # Calculate total completion
        total_completion = completed_distance / total_distance if total_distance > 0 else 0
        
        # Log final summary
        logger.info(f"Route processing summary:")
        logger.info(f"Total segments: {total_segments}")
        logger.info(f"Completed segments: {len(completed_segments)}")
        logger.info(f"Total distance: {total_distance * 111:.2f}km")
        logger.info(f"Completed distance: {completed_distance * 111:.2f}km")
        logger.info(f"Total completion: {total_completion:.2%}")
        
        return jsonify({
            "completed_segments": completed_segments,
            "incomplete_segments": incomplete_segments,
            "total_completion": total_completion,
            "total_distance": total_distance,
            "completed_distance": completed_distance,
            "activities": activity_features
        })
        
    except Exception as e:
        logger.error(f"Error checking route completion: {str(e)}")
        return jsonify({'error': str(e)}), 500

@routes.route('/strava/segments')
@login_required
def get_segments():
    """Get Strava segments within the given bounds."""
    try:
        bounds = json.loads(request.args.get('bounds'))
        access_token = session['strava_token']['access_token']
        
        # Get segments in the area
        segments_data = StravaService.get_segments(bounds, access_token)
        if not segments_data:
            return jsonify({'error': 'Failed to fetch segments'}), 500
        
        # Get athlete's completed segments
        athlete_segments = StravaService.get_athlete_segments(access_token)
        completed_segment_ids = set()
        if athlete_segments:
            completed_segment_ids = {segment['id'] for segment in athlete_segments}
        
        # Process segments
        segments = []
        for segment in segments_data.get('segments', []):
            try:
                segment_info = {
                    'id': segment.get('id'),
                    'name': segment.get('name', 'Unnamed Segment'),
                    'distance': segment.get('distance', 0),
                    'total_elevation_gain': segment.get('elevation_gain', 0),
                    'points': decode_polyline(segment.get('points', '')),
                    'completed': segment.get('id') in completed_segment_ids
                }
                segments.append(segment_info)
            except Exception as e:
                logger.warning(f"Error processing segment {segment.get('id')}: {str(e)}")
                continue
        
        logger.info(f"Successfully processed {len(segments)} segments")
        return jsonify({
            'segments': segments
        })
        
    except Exception as e:
        logger.error(f"Error processing segments: {str(e)}")
        return jsonify({'error': str(e)}), 500

@routes.route('/upload', methods=['POST'])
def upload_gpx():
    """Handle GPX file upload."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.endswith('.gpx'):
        return jsonify({'error': 'Only GPX files are allowed'}), 400
    
    try:
        # Parse GPX file to validate it
        gpx = gpxpy.parse(file)
        
        # Generate unique filename
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'uploaded_route_{timestamp}.gpx'
        filepath = os.path.join(settings.UPLOAD_FOLDER, filename)
        
        # Save file
        file.seek(0)
        file.save(filepath)
        
        return jsonify({
            'success': True,
            'message': 'File uploaded successfully',
            'gpx_file': filename
        })
        
    except Exception as e:
        logger.error(f"Error processing uploaded GPX: {str(e)}")
        return jsonify({'error': str(e)}), 500

@routes.route('/route/<filename>/summary')
@login_required
def get_route_summary(filename):
    """Get AI summary for an existing route."""
    try:
        # Verify file exists
        if not os.path.exists(os.path.join(settings.UPLOAD_FOLDER, filename)):
            return jsonify({'error': 'Route file not found'}), 404

        # Get Strava token if available
        strava_token = session.get('strava_token', {}).get('access_token') if 'strava_token' in session else None
        
        # Generate summary
        route_summary = RouteAnalysisService.analyze_route(filename, strava_token)
        if not route_summary:
            return jsonify({'error': 'Failed to generate route summary'}), 500

        return jsonify({
            'success': True,
            'summary': route_summary
        })

    except Exception as e:
        logger.error(f"Error getting route summary: {str(e)}")
        return jsonify({'error': str(e)}), 500

