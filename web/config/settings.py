import os
from dotenv import load_dotenv
import secrets

# Load environment variables
load_dotenv()

# Flask Configuration
SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
if not SECRET_KEY:
    # Generate a random secret key if none is provided
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: Using a randomly generated secret key. This will change on each restart.")
    print("Set FLASK_SECRET_KEY in your .env file for persistence.")

MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True
PERMANENT_SESSION_LIFETIME = 3600  # 1 hour

# Directory Configuration
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
ACTIVITIES_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'activities')

# Strava Configuration
STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = os.getenv('STRAVA_REDIRECT_URI', 'http://localhost:5001/strava/callback')

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ACTIVITIES_FOLDER, exist_ok=True)
