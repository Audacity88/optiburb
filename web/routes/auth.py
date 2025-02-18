from flask import Blueprint, redirect, request, session, url_for, Response, jsonify
from web.utils.logging import logger
from web.services.strava import StravaService
from web.config import settings
from urllib.parse import quote
from datetime import datetime, timedelta
import queue
import json
import uuid

auth = Blueprint('auth', __name__)

# Store progress queues for each session
fetch_progress_queues = {}

@auth.route('/strava/fetch-progress/<session_id>')
def fetch_progress(session_id):
    """Stream activity fetch progress updates."""
    def generate():
        if session_id not in fetch_progress_queues:
            return
        
        q = fetch_progress_queues[session_id]
        try:
            while True:
                message = q.get(timeout=60)  # 1 minute timeout
                yield f"data: {message}\n\n"
        except queue.Empty:
            fetch_progress_queues.pop(session_id, None)
            yield "data: {\"type\": \"done\"}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@auth.route('/strava/fetch-activities')
def fetch_activities():
    """Fetch activities from Strava."""
    if 'strava_token' not in session:
        return jsonify({'error': 'Not authenticated with Strava'}), 401
    
    # Get the session ID for progress tracking
    session_id = session.get('strava_fetch_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        session['strava_fetch_id'] = session_id
    
    # Create progress queue for this session
    progress_queue = queue.Queue()
    fetch_progress_queues[session_id] = progress_queue
    
    def send_progress(step, progress=None, message=None):
        progress_info = {
            'type': 'progress',
            'step': step,
            'progress': progress,
            'message': message
        }
        progress_queue.put(json.dumps(progress_info))
    
    access_token = session['strava_token']['access_token']
    
    send_progress("Activities", 20, "Loading activity data...")
    
    # Try to load existing activities first
    existing_activities, needs_update = StravaService.load_activities_from_disk(access_token)
    
    if needs_update or not existing_activities:
        send_progress("Activities", 40, "Fetching new activities from Strava...")
        logger.info("Fetching new activities from Strava")
        
        # If no existing activities, fetch from a very early date to get all activities
        # If we have activities, only fetch new ones from the last cached activity
        after_time = datetime(2000, 1, 1)  # Set to year 2000 to get all activities
        
        if existing_activities:
            # Get the most recent activity time
            latest_activity = max(existing_activities, 
                                key=lambda x: datetime.strptime(x.get('start_date', '1970-01-01'), '%Y-%m-%dT%H:%M:%SZ'))
            after_time = datetime.strptime(latest_activity.get('start_date', '1970-01-01'), '%Y-%m-%dT%H:%M:%SZ')
        
        logger.info(f"Fetching activities after {after_time}")
        send_progress("Activities", 60, f"Retrieving activities since {after_time.strftime('%Y-%m-%d')}...")
        
        new_activities = StravaService.fetch_new_activities(access_token, after_time)
        
        if new_activities:
            logger.info(f"Fetched {len(new_activities)} new activities")
            send_progress("Activities", 80, f"Processing {len(new_activities)} new activities...")
            
            if existing_activities:
                # Combine existing and new activities
                all_activities = existing_activities + new_activities
                # Remove duplicates based on activity ID
                seen = set()
                unique_activities = []
                for activity in all_activities:
                    if activity['id'] not in seen:
                        seen.add(activity['id'])
                        unique_activities.append(activity)
                activities_to_save = unique_activities
            else:
                activities_to_save = new_activities
            
            # Save the combined activities
            send_progress("Activities", 90, "Saving activities...")
            StravaService.save_activities_to_disk(access_token, activities_to_save)
            logger.info(f"Saved {len(activities_to_save)} activities to disk")
            send_progress("Activities", 100, f"Successfully saved {len(activities_to_save)} activities")
            
            # Send completion message with new activities flag
            progress_queue.put(json.dumps({"type": "done", "new_activities": True}))
            return jsonify({'success': True, 'new_activities': True})
        else:
            logger.warning("No new activities fetched from Strava")
            send_progress("Activities", 100, "No new activities found")
            progress_queue.put(json.dumps({"type": "done", "new_activities": False}))
            return jsonify({'success': True, 'new_activities': False})
    else:
        logger.info("Using cached activities")
        send_progress("Activities", 100, "Using cached activities")
        progress_queue.put(json.dumps({"type": "done", "new_activities": False}))
        return jsonify({'success': True, 'new_activities': False})

@auth.route('/strava/login')
def strava_login():
    """Redirect to Strava authorization page."""
    # Generate a session ID for progress tracking
    session['strava_fetch_id'] = str(uuid.uuid4())
    
    # Build the redirect URI based on the current request
    redirect_uri = request.host_url.rstrip('/') + url_for('auth.strava_callback')
    encoded_redirect_uri = quote(redirect_uri)
    
    auth_url = (
        "https://www.strava.com/oauth/authorize?"
        f"client_id={settings.STRAVA_CLIENT_ID}&"
        "response_type=code&"
        f"redirect_uri={encoded_redirect_uri}&"
        "approval_prompt=force&"
        "scope=activity:read_all"
    )
    logger.info(f"Redirecting to Strava auth URL: {auth_url}")
    return redirect(auth_url)

@auth.route('/strava/callback')
def strava_callback():
    """Handle Strava OAuth callback."""
    logger.info(f"Callback received. Full URL: {request.url}")
    logger.info(f"Request headers: {dict(request.headers)}")
    logger.info(f"Request args: {request.args}")
    
    code = request.args.get('code')
    if not code:
        logger.error("No code received in callback")
        return "Error: No code received", 400
    
    # Exchange the authorization code for an access token
    token_data = StravaService.exchange_token(code)
    if not token_data:
        return "Error: Failed to exchange code for token", 500
    
    # Store the token in the session
    session['strava_token'] = token_data
    logger.info("Successfully stored token in session")
    
    # Redirect to the main page immediately
    return redirect(url_for('index'))

@auth.route('/strava/logout')
def strava_logout():
    """Log out the user by clearing their session."""
    session.clear()
    return redirect(url_for('index'))
