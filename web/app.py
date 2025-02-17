from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, session, url_for
import os
import sys
import logging
from datetime import datetime, timedelta
import argparse
import json
import queue
import threading
import shutil
import gpxpy
import requests
from functools import wraps
from dotenv import load_dotenv
from urllib.parse import quote
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union
import time
import hashlib

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG level
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Force configuration
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Ensure logger level is DEBUG

# Add a stream handler if none exists
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import optiburb
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
from optiburb import Burbing

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
if not app.secret_key:
    logger.warning("No FLASK_SECRET_KEY found in environment, using a default key")
    app.secret_key = '4f8d7b972df4abe259e2d37c7ddbae734dd9f26654e73269910e12f7381f694b'

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['ACTIVITIES_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'activities')
app.config['SESSION_COOKIE_SECURE'] = False  # Allow session cookie over HTTP
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['ACTIVITIES_FOLDER'], exist_ok=True)

logger.info("Flask Configuration:")
logger.info(f"Secret key set: {bool(app.secret_key)}")
logger.info(f"Upload folder: {app.config['UPLOAD_FOLDER']}")
logger.info(f"Session cookie secure: {app.config['SESSION_COOKIE_SECURE']}")

# Strava API Configuration
STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = 'http://localhost:5001/strava/callback'

# Debug logging for environment variables
logger.info(f"Loaded STRAVA_CLIENT_ID: {STRAVA_CLIENT_ID}")
logger.info(f"Loaded STRAVA_REDIRECT_URI: {STRAVA_REDIRECT_URI}")

if not STRAVA_REDIRECT_URI:
    STRAVA_REDIRECT_URI = 'http://localhost:5001/strava/callback'
    logger.warning(f"STRAVA_REDIRECT_URI not found in environment, using default: {STRAVA_REDIRECT_URI}")

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Progress queue for each session
progress_queues = {}

# Activity cache without TTL
activity_cache = {}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'strava_token' not in session:
            return redirect(url_for('strava_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_strava_segments(bounds, access_token):
    """Fetch Strava segments within the given bounds."""
    url = "https://www.strava.com/api/v3/segments/explore"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "bounds": f"{bounds['minLat']},{bounds['minLng']},{bounds['maxLat']},{bounds['maxLng']}",
        "activity_type": "riding"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Strava segments: {str(e)}")
        return None

def get_athlete_segments(access_token):
    """Fetch athlete's completed segments."""
    url = "https://www.strava.com/api/v3/segments/starred"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching athlete segments: {str(e)}")
        return None

@app.route('/')
def index():
    logger.info(f"Session contents: {session}")
    logger.info(f"Is authenticated: {'strava_token' in session}")
    return render_template('index.html')

@app.route('/strava/login')
def strava_login():
    encoded_redirect_uri = quote(STRAVA_REDIRECT_URI)
    auth_url = (
        "https://www.strava.com/oauth/authorize?"
        f"client_id={STRAVA_CLIENT_ID}&"
        "response_type=code&"
        f"redirect_uri={encoded_redirect_uri}&"
        "approval_prompt=force&"
        "scope=activity:read_all"
    )
    logger.info(f"Redirecting to Strava auth URL: {auth_url}")
    return redirect(auth_url)

def get_cache_key(access_token):
    """Generate a unique cache key for the user's activities."""
    # Create a hash of the access token to use as the filename
    return hashlib.sha256(access_token.encode()).hexdigest()

def save_activities_to_disk(access_token, activities):
    """Save activities to a JSON file on disk."""
    cache_key = get_cache_key(access_token)
    file_path = os.path.join(app.config['ACTIVITIES_FOLDER'], f"{cache_key}.json")
    
    try:
        with open(file_path, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'activities': activities
            }, f)
        logger.info(f"Saved {len(activities)} activities to disk")
        return True
    except Exception as e:
        logger.error(f"Error saving activities to disk: {str(e)}")
        return False

def load_activities_from_disk(access_token):
    """Load activities from disk and check if we need to fetch new ones."""
    cache_key = get_cache_key(access_token)
    file_path = os.path.join(app.config['ACTIVITIES_FOLDER'], f"{cache_key}.json")
    
    if not os.path.exists(file_path):
        logger.info("No cached activities file found")
        return None, True  # No cache, need to fetch
        
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        # Check if the cache is older than 24 hours
        cache_time = datetime.fromisoformat(data['timestamp'])
        needs_update = datetime.now() - cache_time > timedelta(hours=24)
        
        if needs_update:
            logger.info("Cached activities are older than 24 hours, will check for new ones")
        
        activities = data['activities']
        logger.info(f"Loaded {len(activities)} activities from disk cache")
        return activities, needs_update
    except Exception as e:
        logger.error(f"Error loading activities from disk: {str(e)}")
        return None, True  # Error reading cache, need to fetch

def fetch_new_activities(access_token, after_time):
    """Fetch only new activities after the given timestamp."""
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "per_page": 200,  # Maximum allowed by Strava
        "after": int(after_time.timestamp())  # Convert to Unix timestamp
    }
    
    new_activities = []
    page = 1
    
    try:
        logger.info("Fetching new activities")
        while True:
            params['page'] = page
            logger.info(f"Fetching page {page} of new activities")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            page_activities = response.json()
            if not page_activities:
                break
                
            new_activities.extend(page_activities)
            logger.info(f"Fetched {len(page_activities)} new activities from page {page}")
            
            if len(page_activities) < params['per_page']:
                break
                
            page += 1
            time.sleep(0.1)  # Rate limiting
        
        logger.info(f"Total new activities fetched: {len(new_activities)}")
        return new_activities
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching new activities: {str(e)}")
        return None

def fetch_and_cache_activities(access_token):
    """Fetch all user activities and store them in both memory and disk cache."""
    # First try to load from disk
    existing_activities, needs_update = load_activities_from_disk(access_token)
    
    if existing_activities and not needs_update:
        # Store in memory cache and return if cache is fresh
        cache_key = get_cache_key(access_token)
        activity_cache[cache_key] = existing_activities
        return existing_activities
    
    if existing_activities and needs_update:
        # We have existing activities but need to check for new ones
        logger.info("Checking for new activities since last cache")
        cache_time = datetime.fromisoformat(json.load(open(
            os.path.join(app.config['ACTIVITIES_FOLDER'], f"{get_cache_key(access_token)}.json")
        ))['timestamp'])
        
        new_activities = fetch_new_activities(access_token, cache_time)
        
        if new_activities:
            # Combine existing and new activities, removing duplicates by ID
            activity_ids = {a['id'] for a in existing_activities}
            unique_new_activities = [a for a in new_activities if a['id'] not in activity_ids]
            
            if unique_new_activities:
                logger.info(f"Found {len(unique_new_activities)} new activities")
                all_activities = unique_new_activities + existing_activities
                
                # Update both caches with combined activities
                cache_key = get_cache_key(access_token)
                activity_cache[cache_key] = all_activities
                save_activities_to_disk(access_token, all_activities)
                
                return all_activities
        
        # If no new activities or error fetching them, use existing cache
        logger.info("No new activities found, using existing cache")
        cache_key = get_cache_key(access_token)
        activity_cache[cache_key] = existing_activities
        return existing_activities
    
    # No existing activities, fetch all
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "per_page": 200  # Maximum allowed by Strava
    }
    
    activities = []
    page = 1
    
    try:
        logger.info("Fetching all user activities")
        while True:
            params['page'] = page
            logger.info(f"Fetching page {page} of activities")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            page_activities = response.json()
            if not page_activities:
                break
                
            activities.extend(page_activities)
            logger.info(f"Fetched {len(page_activities)} activities from page {page}")
            
            if len(page_activities) < params['per_page']:
                break
                
            page += 1
            time.sleep(0.1)
        
        logger.info(f"Total activities fetched: {len(activities)}")
        
        if activities:
            # Store in both caches
            cache_key = get_cache_key(access_token)
            activity_cache[cache_key] = activities
            save_activities_to_disk(access_token, activities)
        
        return activities
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching user activities: {str(e)}")
        return None

def get_user_activities(access_token, bounds):
    """Get user activities within the given bounds from cache."""
    cache_key = get_cache_key(access_token)
    
    # Try memory cache first
    activities = activity_cache.get(cache_key)
    
    # If not in memory, try disk cache
    if not activities:
        activities, _ = load_activities_from_disk(access_token)
        if activities:
            # Store in memory cache for future use
            activity_cache[cache_key] = activities
    
    if not activities:
        logger.warning("No activities found in cache - they should have been pre-loaded")
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

@app.route('/strava/callback')
def strava_callback():
    logger.info(f"Callback received. Full URL: {request.url}")
    logger.info(f"Request headers: {dict(request.headers)}")
    logger.info(f"Request args: {request.args}")
    
    if request.headers.get('Host', '').startswith('127.0.0.1'):
        original_url = request.url
        redirected_url = original_url.replace('127.0.0.1', 'localhost')
        logger.info(f"Redirecting from {original_url} to {redirected_url}")
        return redirect(redirected_url)
    
    code = request.args.get('code')
    if not code:
        logger.error("No code received in callback")
        return "Error: No code received", 400
    
    try:
        # Exchange the authorization code for an access token
        token_url = "https://www.strava.com/oauth/token"
        data = {
            'client_id': STRAVA_CLIENT_ID,
            'client_secret': STRAVA_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code'
        }
        
        logger.info("Exchanging authorization code for token")
        logger.info(f"Token request data: {data}")
        
        response = requests.post(token_url, data=data)
        logger.info(f"Token response status: {response.status_code}")
        logger.info(f"Token response: {response.text}")
        response.raise_for_status()
        
        # Store the token in the session
        token_data = response.json()
        session['strava_token'] = token_data
        logger.info("Successfully stored token in session")
        
        # Fetch and cache activities immediately after getting the token
        access_token = token_data['access_token']
        cache_key = get_cache_key(access_token)
        
        if cache_key not in activity_cache:
            logger.info("Fetching initial activities cache")
            activities = fetch_and_cache_activities(access_token)
            if not activities:
                logger.error("Failed to fetch initial activities")
                # Continue anyway - we'll try again when needed
            else:
                logger.info(f"Successfully cached {len(activities)} activities")
        else:
            logger.info("Activities already cached")
        
        # Redirect to the main page
        return redirect(url_for('index'))
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error exchanging code for token: {str(e)}")
        return f"Error: Failed to exchange code for token - {str(e)}", 500

class ProgressHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        try:
            # Extract relevant information from the log record
            msg = self.format(record)
            
            # Parse progress information from specific log messages
            progress_info = {
                'type': 'progress',
                'message': msg,
                'step': None,
                'progress': None
            }

            # Check for specific progress messages
            if 'dijkstra progress' in msg:
                try:
                    progress = int(msg.split('%')[0].split('progress ')[-1])
                    progress_info.update({
                        'step': 'Calculating shortest paths',
                        'progress': progress
                    })
                except:
                    pass
            elif 'searching for query' in msg:
                progress_info.update({
                    'step': 'Geocoding location',
                    'progress': 10
                })
            elif 'fetching OSM data' in msg:
                progress_info.update({
                    'step': 'Fetching map data',
                    'progress': 20
                })
            elif 'converting directed graph to undirected' in msg:
                progress_info.update({
                    'step': 'Processing graph',
                    'progress': 40
                })
            elif 'calculating max weight matching' in msg:
                progress_info.update({
                    'step': 'Calculating optimal route',
                    'progress': 70
                })
            elif 'augment original graph' in msg:
                progress_info.update({
                    'step': 'Finalizing route',
                    'progress': 90
                })
            
            self.queue.put(json.dumps(progress_info))
        except Exception as e:
            logger.error(f"Error in progress handler: {str(e)}")

@app.route('/progress/<session_id>')
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

@app.route('/generate', methods=['POST'])
def generate_route():
    try:
        data = request.get_json()
        location = data.get('location')
        start_point = data.get('start_point')
        session_id = data.get('session_id')
        
        # Create progress queue for this session
        progress_queue = queue.Queue()
        progress_queues[session_id] = progress_queue
        
        # Create progress handler
        progress_handler = ProgressHandler(progress_queue)
        logger.addHandler(progress_handler)

        # Check for Strava authentication and cached activities
        if 'strava_token' in session:
            access_token = session['strava_token']['access_token']
            cache_key = get_cache_key(access_token)
            
            if cache_key in activity_cache:
                activities = activity_cache[cache_key]
                logger.info(f"Using {len(activities)} existing cached activities")
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'step': 'Loading Strava activities',
                    'progress': 4,
                    'message': f'Using {len(activities)} cached activities'
                }))
            else:
                logger.info("No activities in cache, fetching from Strava")
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'step': 'Loading Strava activities',
                    'progress': 2,
                    'message': 'Fetching your activities...'
                }))
                
                activities = fetch_and_cache_activities(access_token)
                if not activities:
                    logger.warning("Failed to fetch activities from Strava")
                    return jsonify({'error': 'Failed to load Strava activities'}), 500
                
                logger.info(f"Successfully cached {len(activities)} activities")
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'step': 'Loading Strava activities',
                    'progress': 4,
                    'message': f'Successfully loaded {len(activities)} activities'
                }))
        
        # Convert dictionary to argparse.Namespace
        options = argparse.Namespace(
            simplify=data.get('simplify', False),
            prune=data.get('prune', False),
            simplify_gpx=data.get('simplify_gpx', True),
            feature_deadend=data.get('feature_deadend', False),
            exclude_completed=data.get('exclude_completed', False),
            debug='info',
            start=start_point,
            names=[location],
            select=1,
            buffer=20,
            shapefile=None,
            save_fig=False,
            save_boundary=False,
            complex_gpx=not data.get('simplify_gpx', True)
        )
        
        if not location:
            return jsonify({'error': 'Location is required'}), 400

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
        if start_point:
            burbing.set_start_location(start_point)
        
        # If exclude_completed is enabled and user is authenticated with Strava
        if options.exclude_completed and 'strava_token' in session:
            try:
                logger.info("Excluding completed roads from route generation")
                progress_queue.put(json.dumps({
                    'type': 'progress',
                    'step': 'Processing Strava data',
                    'progress': 10,
                    'message': 'Checking completed roads...'
                }))
                
                # Get the bounds of the polygon
                bounds = {
                    'minLat': polygon.bounds[1],
                    'maxLat': polygon.bounds[3],
                    'minLng': polygon.bounds[0],
                    'maxLng': polygon.bounds[2]
                }
                
                # Get user's activities in the area
                activities = get_user_activities(session['strava_token']['access_token'], bounds)
                if not activities:
                    logger.warning("No activities found in the area")
                    progress_queue.put(json.dumps({
                        'type': 'progress',
                        'step': 'Processing Strava data',
                        'progress': 15,
                        'message': 'No activities found in this area'
                    }))
                else:
                    logger.info(f"Found {len(activities)} activities in the area")
                    # Create a list of completed road areas
                    completed_roads = []
                    for activity in activities:
                        if activity.get('map', {}).get('summary_polyline'):
                            points = decode_polyline(activity['map']['summary_polyline'])
                            if points:
                                try:
                                    # Convert points to LineString
                                    line_coords = [[lng, lat] for lat, lng in points]
                                    line = LineString(line_coords)
                                    # Buffer the line to create an area (20 meters wide - reduced from 40)
                                    buffered_line = line.buffer(0.0002)  # Reduced buffer size
                                    completed_roads.append(buffered_line)
                                except Exception as e:
                                    logger.warning(f"Error processing activity {activity.get('id')}: {str(e)}")
                                    continue
                    
                    if completed_roads:
                        try:
                            logger.info(f"Combining {len(completed_roads)} completed road areas")
                            completed_area = unary_union(completed_roads)
                            # Store original area for comparison
                            original_area = polygon.area
                            # Subtract completed roads from the polygon
                            polygon = polygon.difference(completed_area)
                            
                            # Check if we haven't excluded too much
                            if polygon.area < (original_area * 0.1):  # If less than 10% remains
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
                                logger.info("Successfully excluded completed roads from route area")
                                progress_queue.put(json.dumps({
                                    'type': 'progress',
                                    'step': 'Processing Strava data',
                                    'progress': 15,
                                    'message': f'Excluded {len(completed_roads)} completed road sections'
                                }))
                        except Exception as e:
                            logger.error(f"Error processing completed roads: {str(e)}", exc_info=True)
                            progress_queue.put(json.dumps({
                                'type': 'error',
                                'message': f'Error excluding completed roads: {str(e)}'
                            }))
                            return jsonify({'error': f'Error excluding completed roads: {str(e)}'}), 500
            except Exception as e:
                logger.error(f"Error in Strava processing: {str(e)}", exc_info=True)
                progress_queue.put(json.dumps({
                    'type': 'error',
                    'message': f'Error processing Strava data: {str(e)}'
                }))
                return jsonify({'error': f'Error processing Strava data: {str(e)}'}), 500

        try:
            # Load and process the graph
            burbing.load(options)
            
            # If we have completed roads to exclude, filter the graph after loading
            if options.exclude_completed and hasattr(burbing, 'completed_area'):
                logger.info("Filtering graph to exclude completed roads")
                edges_to_remove = []
                total_edges = len(burbing.g.edges())
                edges_processed = 0
                
                # Create a buffer around the completed area for more reliable intersection checks
                completed_area_buffer = burbing.completed_area.buffer(0.00005)  # ~5 meter buffer
                
                # Process edges in batches for progress updates
                batch_size = max(1, total_edges // 10)  # Update progress every 10%
                
                for u, v, data in burbing.g.edges(data=True):
                    edges_processed += 1
                    
                    # Update progress every batch_size edges
                    if edges_processed % batch_size == 0:
                        progress = int((edges_processed / total_edges) * 100)
                        progress_queue.put(json.dumps({
                            'type': 'progress',
                            'step': 'Processing graph',
                            'progress': 15 + (progress // 20),  # Scale from 15-20%
                            'message': f'Checking road segments: {progress}%'
                        }))
                    
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
                
                # Final progress update and edge removal
                if edges_to_remove:
                    if len(edges_to_remove) < (total_edges * 0.9):
                        burbing.g.remove_edges_from(edges_to_remove)
                        removed_percentage = (len(edges_to_remove) / total_edges) * 100
                        logger.info(f"Removed {len(edges_to_remove)} completed road edges ({removed_percentage:.1f}%) from graph")
                        progress_queue.put(json.dumps({
                            'type': 'progress',
                            'step': 'Processing graph',
                            'progress': 20,
                            'message': f'Excluded {len(edges_to_remove)} road segments ({removed_percentage:.1f}% of total)'
                        }))
                    else:
                        logger.warning(f"Too many edges would be removed ({len(edges_to_remove)} of {total_edges}), keeping original graph")
                        progress_queue.put(json.dumps({
                            'type': 'progress',
                            'step': 'Processing graph',
                            'progress': 20,
                            'message': 'Too many completed roads, using original network'
                        }))
                else:
                    logger.info("No completed road edges found to remove")
                    progress_queue.put(json.dumps({
                        'type': 'progress',
                        'step': 'Processing graph',
                        'progress': 20,
                        'message': 'No completed road segments found in this area'
                    }))
            
            if options.prune:
                burbing.prune()
            
            burbing.determine_nodes()
            
            if options.feature_deadend:
                burbing.optimise_dead_ends()
            
            burbing.determine_combinations()
            burbing.determine_circuit()
            
            # Format location string to match the Burbing class format
            formatted_location = location.lower().replace(' ', '_').replace(',', '')
            
            # Generate GPX file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            gpx_filename = f'burb_track_{formatted_location}_{timestamp}.gpx'
            
            # Create GPX file
            burbing.create_gpx_track(burbing.g_augmented, burbing.euler_circuit, options.simplify_gpx)
            
        except Exception as e:
            logger.error(f"Error in route generation: {str(e)}", exc_info=True)
            progress_queue.put(json.dumps({
                'type': 'error',
                'message': f'Error generating route: {str(e)}'
            }))
            return jsonify({'error': f'Error generating route: {str(e)}'}), 500
        
        # The file will be created in the current directory (web/)
        # Look for the most recently created GPX file that matches our location
        gpx_files = [f for f in os.listdir(os.path.dirname(os.path.abspath(__file__))) 
                     if f.startswith(f'burb_track_{formatted_location}_') and f.endswith('.gpx')]
        if not gpx_files:
            logger.error("No GPX file found")
            raise FileNotFoundError("Generated GPX file not found")
            
        # Sort by creation time and get the most recent
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                               sorted(gpx_files, 
                                     key=lambda x: os.path.getctime(
                                         os.path.join(os.path.dirname(os.path.abspath(__file__)), x)
                                     ))[-1])
        dst_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(src_path))
        
        # Move file to uploads directory
        shutil.move(src_path, dst_path)
        
        # Update gpx_filename to match the actual file
        gpx_filename = os.path.basename(src_path)
        
        progress_queue.put(json.dumps({
            'type': 'progress',
            'step': 'Route generation complete',
            'progress': 100,
            'message': 'Route generated successfully!'
        }))
        
        # Remove progress handler
        logger.removeHandler(progress_handler)
        
        return jsonify({
            'success': True,
            'message': 'Route generated successfully',
            'gpx_file': gpx_filename
        })
        
    except Exception as e:
        logger.error(f"Error generating route: {str(e)}", exc_info=True)
        if session_id in progress_queues:
            progress_queues[session_id].put(json.dumps({
                'type': 'error',
                'message': str(e)
            }))
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {filename}")
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}", exc_info=True)
        return jsonify({'error': 'File not found'}), 404

@app.route('/route/<filename>')
def get_route_data(filename):
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
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
            return jsonify({'error': 'No valid route data found'}), 400
        
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
        
        response_data = {
            "geojson": geojson,
            "bounds": bounds
        }
        logger.info("Successfully prepared route data")
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error getting route data: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/strava/segments')
@login_required
def get_segments():
    """Get Strava segments within the given bounds."""
    try:
        bounds = json.loads(request.args.get('bounds'))
        access_token = session['strava_token']['access_token']
        
        # Get segments in the area
        segments_data = get_strava_segments(bounds, access_token)
        if not segments_data:
            return jsonify({'error': 'Failed to fetch segments'}), 500
        
        # Get athlete's completed segments
        athlete_segments = get_athlete_segments(access_token)
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
                    'total_elevation_gain': segment.get('elevation_gain', 0),  # Changed from total_elevation_gain
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
        logger.error(f"Error processing segments: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

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

def create_activity_map(activities):
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
                    # Increase buffer size to 40 meters (roughly 0.0004 degrees)
                    buffered_line = line.buffer(0.0004)
                    activity_lines.append(buffered_line)
                    logger.debug(f"Added activity {i+1} to map with {len(coords)} points")
                    # Log the bounds of this activity
                    bounds = line.bounds
                    logger.debug(f"Activity {i+1} bounds: minLng={bounds[0]}, minLat={bounds[1]}, maxLng={bounds[2]}, maxLat={bounds[3]}")
                except Exception as e:
                    logger.warning(f"Error processing activity {i+1}: {str(e)}")
                    continue
    
    if activity_lines:
        logger.info(f"Successfully processed {len(activity_lines)} activities for the map")
        try:
            combined_map = unary_union(activity_lines)
            combined_bounds = combined_map.bounds
            logger.info(f"Successfully created unified activity map with bounds: minLng={combined_bounds[0]}, minLat={combined_bounds[1]}, maxLng={combined_bounds[2]}, maxLat={combined_bounds[3]}")
            return combined_map
        except Exception as e:
            logger.error(f"Error creating unified activity map: {str(e)}")
            return None
    else:
        logger.warning("No valid activities found to create map")
        return None

@app.route('/route/<filename>/completion')
@login_required
def get_route_completion(filename):
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
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
        logger.info("Fetching activities within bounds...")
        activities = get_user_activities(access_token, bounds)
        
        if not activities:
            logger.warning("No activities found in the area")
            return jsonify({
                "completed_segments": [],
                "incomplete_segments": [],
                "total_completion": 0,
                "total_distance": 0,
                "completed_distance": 0,
                "activities": []
            })
        
        logger.info(f"Found {len(activities)} activities in the area")
        
        # Process activities for display
        activity_features = []
        for i, activity in enumerate(activities):
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
        activity_map = create_activity_map(activities)
        
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
        
        # Break the route into smaller segments
        for track in gpx.tracks:
            for segment in track.segments:
                points = segment.points
                for i in range(len(points) - 1):
                    start_point = points[i]
                    end_point = points[i + 1]
                    
                    coords = [
                        [start_point.longitude, start_point.latitude],
                        [end_point.longitude, end_point.latitude]
                    ]
                    
                    route_segment = LineString(coords)
                    segment_length = route_segment.length
                    total_distance += segment_length
                    
                    try:
                        intersects = route_segment.intersects(activity_map)
                        
                        if intersects:
                            intersection = route_segment.intersection(activity_map)
                            intersection_length = intersection.length if hasattr(intersection, 'length') else 0
                            completion_ratio = intersection_length / segment_length
                            
                            if completion_ratio > 0.9:
                                completed_segments.append({
                                    "coordinates": coords,
                                    "completion": 1.0
                                })
                                completed_distance += segment_length
                            else:
                                incomplete_segments.append({
                                    "coordinates": coords,
                                    "completion": completion_ratio
                                })
                                completed_distance += (segment_length * completion_ratio)
                        else:
                            incomplete_segments.append({
                                "coordinates": coords,
                                "completion": 0.0
                            })
                    except Exception as e:
                        logger.error(f"Error processing segment: {str(e)}")
                        incomplete_segments.append({
                            "coordinates": coords,
                            "completion": 0.0
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
        logger.error(f"Error checking route completion: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001) 