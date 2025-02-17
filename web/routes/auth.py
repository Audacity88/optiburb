from flask import Blueprint, redirect, request, session, url_for
from web.utils.logging import logger
from web.services.strava import StravaService
from web.config import settings
from urllib.parse import quote

auth = Blueprint('auth', __name__)

@auth.route('/strava/login')
def strava_login():
    """Redirect to Strava authorization page."""
    encoded_redirect_uri = quote(settings.STRAVA_REDIRECT_URI)
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
    
    if request.headers.get('Host', '').startswith('127.0.0.1'):
        original_url = request.url
        redirected_url = original_url.replace('127.0.0.1', 'localhost')
        logger.info(f"Redirecting from {original_url} to {redirected_url}")
        return redirect(redirected_url)
    
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
    
    # Fetch and cache activities immediately after getting the token
    access_token = token_data['access_token']
    cache_key = StravaService.get_cache_key(access_token)
    
    # Redirect to the main page
    return redirect(url_for('index'))
