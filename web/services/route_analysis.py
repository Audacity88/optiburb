import gpxpy
import requests
from datetime import datetime, timedelta
from web.utils.logging import logger
from web.services.strava import StravaService
from web.config import settings
import statistics
import json

class RouteAnalysisService:
    @staticmethod
    def analyze_route(gpx_filename, strava_token=None):
        """
        Analyze a route and generate an AI summary including length, time estimates,
        hilliness, safety, and contextual alerts.
        """
        try:
            with open(f"{settings.UPLOAD_FOLDER}/{gpx_filename}", 'r') as gpx_file:
                gpx = gpxpy.parse(gpx_file)

            # Calculate basic route statistics
            stats = gpx.get_moving_data()
            total_distance = stats.moving_distance  # in meters

            # Calculate elevation data
            elevation_gain = 0
            elevation_loss = 0
            elevation_changes = []
            points_with_elevation = 0
            
            for track in gpx.tracks:
                for segment in track.segments:
                    for i in range(1, len(segment.points)):
                        curr_point = segment.points[i]
                        prev_point = segment.points[i-1]
                        
                        curr_elevation = getattr(curr_point, 'elevation', None)
                        prev_elevation = getattr(prev_point, 'elevation', None)
                        
                        logger.debug(f"Point {i}: Current elevation = {curr_elevation}, Previous elevation = {prev_elevation}")
                        
                        if curr_elevation is not None and prev_elevation is not None:
                            points_with_elevation += 1
                            elevation_change = curr_elevation - prev_elevation
                            elevation_changes.append(abs(elevation_change))
                            
                            if elevation_change > 0:
                                elevation_gain += elevation_change
                            else:
                                elevation_loss += abs(elevation_change)

            # Calculate hilliness score (0-100)
            if points_with_elevation > 0:
                avg_elevation_change = statistics.mean(elevation_changes) if elevation_changes else 0
                hilliness_score = min(100, (avg_elevation_change / 10) * 100)  # Normalize to 0-100
            else:
                logger.warning("No elevation data found in GPX file")
                avg_elevation_change = 0
                hilliness_score = 0

            # Estimate completion time based on Strava history
            estimated_time = RouteAnalysisService._estimate_completion_time(total_distance, strava_token)

            # Get safety information
            safety_info = RouteAnalysisService._analyze_safety(gpx)

            # Get contextual alerts
            alerts = RouteAnalysisService._get_contextual_alerts(gpx)

            logger.debug(f"Route analysis complete: Distance={total_distance}m, Elevation gain={elevation_gain}m, loss={elevation_loss}m")

            summary = {
                "distance": {
                    "meters": total_distance,
                    "kilometers": round(total_distance / 1000, 2),
                    "miles": round(total_distance / 1609.34, 2)
                },
                "elevation": {
                    "gain_meters": round(elevation_gain, 1),
                    "loss_meters": round(elevation_loss, 1),
                    "net_meters": round(elevation_gain - elevation_loss, 1)
                },
                "hilliness": {
                    "score": round(hilliness_score, 1),
                    "description": RouteAnalysisService._get_hilliness_description(hilliness_score)
                },
                "estimated_time": estimated_time,
                "safety": safety_info,
                "alerts": alerts
            }

            return summary

        except Exception as e:
            logger.error(f"Error analyzing route: {str(e)}")
            return None

    @staticmethod
    def _estimate_completion_time(distance_meters, strava_token):
        """Estimate completion time based on user's Strava history."""
        if not strava_token:
            # Use default estimates if no Strava data available
            walking_pace = 5.0  # km/h
            cycling_pace = 15.0  # km/h
            running_pace = 10.0  # km/h
        else:
            try:
                activities = StravaService.load_activities_from_disk(strava_token)[0]
                
                # Calculate average speeds for different activity types
                speeds = {"Walk": [], "Run": [], "Ride": []}
                for activity in activities:
                    if activity["type"] in speeds and activity.get("average_speed"):
                        speeds[activity["type"]].append(activity["average_speed"])

                walking_pace = statistics.mean(speeds["Walk"]) * 3.6 if speeds["Walk"] else 5.0
                running_pace = statistics.mean(speeds["Run"]) * 3.6 if speeds["Run"] else 10.0
                cycling_pace = statistics.mean(speeds["Ride"]) * 3.6 if speeds["Ride"] else 15.0

            except Exception as e:
                logger.error(f"Error getting Strava data for time estimation: {str(e)}")
                walking_pace = 5.0
                cycling_pace = 15.0
                running_pace = 10.0

        distance_km = distance_meters / 1000
        return {
            "walking": {
                "hours": round(distance_km / walking_pace, 1),
                "pace_kmh": round(walking_pace, 1)
            },
            "running": {
                "hours": round(distance_km / running_pace, 1),
                "pace_kmh": round(running_pace, 1)
            },
            "cycling": {
                "hours": round(distance_km / cycling_pace, 1),
                "pace_kmh": round(cycling_pace, 1)
            }
        }

    @staticmethod
    def _analyze_safety(gpx):
        """Analyze route safety based on available data."""
        safety_score = 0
        safety_factors = []

        try:
            # Count segments with bike lanes or pedestrian paths
            total_segments = 0
            safe_segments = 0
            
            for track in gpx.tracks:
                for segment in track.segments:
                    total_segments += 1
                    # Check if segment has bike lane or pedestrian path
                    # This would require integration with OpenStreetMap data
                    # For now, using placeholder logic
                    if hasattr(segment, "type") and segment.type in ["cycleway", "footway", "path"]:
                        safe_segments += 1

            if total_segments > 0:
                safety_score = (safe_segments / total_segments) * 100

            # Add safety factors based on analysis
            safety_factors.append({
                "factor": "Dedicated Paths",
                "description": f"{round(safe_segments/total_segments * 100)}% of route on dedicated paths"
            })

            # Additional safety factors could be added here
            # - Traffic volume data
            # - Crime statistics
            # - Lighting conditions
            # - Road conditions

        except Exception as e:
            logger.error(f"Error analyzing safety: {str(e)}")

        return {
            "score": round(safety_score, 1),
            "description": RouteAnalysisService._get_safety_description(safety_score),
            "factors": safety_factors
        }

    @staticmethod
    def _get_contextual_alerts(gpx):
        """Get contextual alerts for the route."""
        alerts = []

        try:
            # Get route bounds
            bounds = gpx.get_bounds()
            
            # Check weather forecast
            weather_alert = RouteAnalysisService._check_weather(
                bounds.min_latitude,
                bounds.min_longitude,
                bounds.max_latitude,
                bounds.max_longitude
            )
            if weather_alert:
                alerts.append(weather_alert)

            # Check for road work and maintenance
            maintenance_alert = RouteAnalysisService._check_maintenance(
                bounds.min_latitude,
                bounds.min_longitude,
                bounds.max_latitude,
                bounds.max_longitude
            )
            if maintenance_alert:
                alerts.append(maintenance_alert)

            # Check for special events
            events_alert = RouteAnalysisService._check_events(
                bounds.min_latitude,
                bounds.min_longitude,
                bounds.max_latitude,
                bounds.max_longitude
            )
            if events_alert:
                alerts.append(events_alert)

        except Exception as e:
            logger.error(f"Error getting contextual alerts: {str(e)}")

        return alerts

    @staticmethod
    def _check_weather(min_lat, min_lon, max_lat, max_lon):
        """Check weather forecast for the route area."""
        try:
            # Use OpenWeatherMap API or similar service
            # This is a placeholder - you'll need to implement the actual API call
            weather_data = {
                "type": "weather",
                "severity": "info",
                "message": "Weather data not available"
            }
            return weather_data
        except Exception as e:
            logger.error(f"Error checking weather: {str(e)}")
            return None

    @staticmethod
    def _check_maintenance(min_lat, min_lon, max_lat, max_lon):
        """Check for road maintenance and construction in the route area."""
        try:
            # Use local government APIs or OpenStreetMap data
            # This is a placeholder - you'll need to implement the actual API call
            maintenance_data = {
                "type": "maintenance",
                "severity": "info",
                "message": "Maintenance data not available"
            }
            return maintenance_data
        except Exception as e:
            logger.error(f"Error checking maintenance: {str(e)}")
            return None

    @staticmethod
    def _check_events(min_lat, min_lon, max_lat, max_lon):
        """Check for special events along the route."""
        try:
            # Use event APIs or local data sources
            # This is a placeholder - you'll need to implement the actual API call
            events_data = {
                "type": "events",
                "severity": "info",
                "message": "Events data not available"
            }
            return events_data
        except Exception as e:
            logger.error(f"Error checking events: {str(e)}")
            return None

    @staticmethod
    def _get_hilliness_description(score):
        """Get a descriptive text for the hilliness score."""
        if score < 20:
            return "Mostly flat"
        elif score < 40:
            return "Gently rolling"
        elif score < 60:
            return "Moderately hilly"
        elif score < 80:
            return "Hilly"
        else:
            return "Very hilly"

    @staticmethod
    def _get_safety_description(score):
        """Get a descriptive text for the safety score."""
        if score < 20:
            return "Use extreme caution"
        elif score < 40:
            return "Exercise caution"
        elif score < 60:
            return "Moderately safe"
        elif score < 80:
            return "Generally safe"
        else:
            return "Very safe" 