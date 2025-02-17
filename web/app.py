from flask import Flask, render_template, request, jsonify, send_file, Response
import os
import sys
import logging
from datetime import datetime
import argparse
import json
import queue
import threading
import shutil
import gpxpy

# Add parent directory to path to import optiburb
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
from optiburb import Burbing

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Progress queue for each session
progress_queues = {}

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

@app.route('/')
def index():
    return render_template('index.html')

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
        
        # Convert dictionary to argparse.Namespace
        options = argparse.Namespace(
            simplify=data.get('simplify', False),
            prune=data.get('prune', False),
            simplify_gpx=data.get('simplify_gpx', True),
            feature_deadend=data.get('feature_deadend', False),
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
        
        # Load and process the graph
        burbing.load(options)
        
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
        
        # The file will be created in the current directory (web/)
        # Look for the most recently created GPX file that matches our location
        gpx_files = [f for f in os.listdir(os.path.dirname(os.path.abspath(__file__))) 
                     if f.startswith(f'burb_track_{formatted_location}_') and f.endswith('.gpx')]
        if not gpx_files:
            logger.error("No GPX file found")
            raise FileNotFoundError("Generated GPX file not found")
            
        # Sort by creation time and get the most recent
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                               sorted(gpx_files, key=lambda x: os.path.getctime(
                                   os.path.join(os.path.dirname(os.path.abspath(__file__)), x)))[-1])
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
            raise FileNotFoundError(f"File not found: {filename}")
        
        # Parse GPX file
        with open(file_path, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
        
        # Convert to GeoJSON
        features = []
        for track in gpx.tracks:
            for segment in track.segments:
                coordinates = [[point.longitude, point.latitude] for point in segment.points]
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
        else:
            bounds = None
        
        return jsonify({
            "geojson": geojson,
            "bounds": bounds
        })
        
    except Exception as e:
        logger.error(f"Error getting route data: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000) 