import logging
import json
import queue
from logging import Handler

def setup_logging():
    """Configure logging for the application."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        force=True
    )
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # Add a stream handler if none exists
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

class ProgressHandler(Handler):
    """Custom logging handler for progress updates."""
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

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
