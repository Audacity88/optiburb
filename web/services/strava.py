import requests
import hashlib
import json
import time
from datetime import datetime, timedelta
import os
from web.utils.logging import logger
from web.config import settings

class StravaService:
    @staticmethod
    def get_cache_key(access_token):
        """Generate a unique cache key for the user's activities."""
        return hashlib.sha256(access_token.encode()).hexdigest()

    @staticmethod
    def save_activities_to_disk(access_token, activities):
        """Save activities to a JSON file on disk."""
        cache_key = StravaService.get_cache_key(access_token)
        file_path = os.path.join(settings.ACTIVITIES_FOLDER, f"{cache_key}.json")
        
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

    @staticmethod
    def load_activities_from_disk(access_token):
        """Load activities from disk and check if we need to fetch new ones."""
        cache_key = StravaService.get_cache_key(access_token)
        file_path = os.path.join(settings.ACTIVITIES_FOLDER, f"{cache_key}.json")
        
        if not os.path.exists(file_path):
            logger.info("No cached activities file found")
            return None, True
            
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                
            # Check if the cache is older than 24 hours
            cache_time = datetime.fromisoformat(data['timestamp'])
            needs_update = datetime.now() - cache_time > timedelta(hours=24)
            
            if needs_update:
                logger.info("Cached activities are older than 24 hours")
            
            activities = data['activities']
            logger.info(f"Loaded {len(activities)} activities from disk cache")
            return activities, needs_update
        except Exception as e:
            logger.error(f"Error loading activities from disk: {str(e)}")
            return None, True

    @staticmethod
    def fetch_new_activities(access_token, after_time):
        """Fetch only new activities after the given timestamp."""
        url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "per_page": 200,
            "after": int(after_time.timestamp())
        }
        
        new_activities = []
        page = 1
        
        try:
            logger.info("Fetching new activities")
            while True:
                params['page'] = page
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                
                page_activities = response.json()
                if not page_activities:
                    break
                    
                new_activities.extend(page_activities)
                
                if len(page_activities) < params['per_page']:
                    break
                    
                page += 1
                time.sleep(0.1)  # Rate limiting
            
            logger.info(f"Total new activities fetched: {len(new_activities)}")
            return new_activities
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching new activities: {str(e)}")
            return None

    @staticmethod
    def get_segments(bounds, access_token):
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

    @staticmethod
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

    @staticmethod
    def exchange_token(code):
        """Exchange authorization code for access token."""
        token_url = "https://www.strava.com/oauth/token"
        data = {
            'client_id': settings.STRAVA_CLIENT_ID,
            'client_secret': settings.STRAVA_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code'
        }
        
        try:
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error exchanging code for token: {str(e)}")
            return None
