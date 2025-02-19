import logging
import json
import queue
from logging import Handler
import sys

def setup_logging():
    """Configure logging for the application."""
    # Get the logger
    logger = logging.getLogger('web.utils.logging')
    
    # Clear any existing handlers
    logger.handlers = []
    
    # Set the level
    logger.setLevel(logging.INFO)

    # Create console handler and set level to debug
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Add formatter to console handler
    console_handler.setFormatter(formatter)

    # Add console handler to logger
    logger.addHandler(console_handler)

    # Set specific log levels for different components
    logging.getLogger('web.core.route').setLevel(logging.INFO)
    logging.getLogger('web.core.geometry').setLevel(logging.INFO)
    logging.getLogger('web.services.route').setLevel(logging.INFO)

    # Suppress detailed logging from libraries
    logging.getLogger('gpxpy').setLevel(logging.WARNING)
    logging.getLogger('networkx').setLevel(logging.WARNING)
    logging.getLogger('shapely').setLevel(logging.WARNING)

    # Prevent propagation to root logger to avoid duplicates
    logger.propagate = False

    return logger

class ProgressHandler(Handler):
    """Custom logging handler for progress updates."""
    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        try:
            msg = self.format(record)
            progress_info = {
                'type': 'progress',
                'message': msg,
                'step': None,
                'progress': None
            }

            # Parse progress information from specific log messages
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
            logging.error(f"Error in progress handler: {str(e)}")

# Create a global logger instance
logger = setup_logging()
