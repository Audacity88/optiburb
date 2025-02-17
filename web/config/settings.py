import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Flask Configuration
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', '4f8d7b972df4abe259e2d37c7ddbae734dd9f26654e73269910e12f7381f694b')
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
