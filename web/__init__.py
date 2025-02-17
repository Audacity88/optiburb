from flask import Flask, render_template
from web.config import settings
from web.utils.logging import logger
from web.routes.auth import auth
from web.routes.route_generation import routes

def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    
    # Load configuration
    app.secret_key = settings.SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = settings.MAX_CONTENT_LENGTH
    app.config['UPLOAD_FOLDER'] = settings.UPLOAD_FOLDER
    app.config['SESSION_COOKIE_SECURE'] = settings.SESSION_COOKIE_SECURE
    app.config['SESSION_COOKIE_HTTPONLY'] = settings.SESSION_COOKIE_HTTPONLY
    app.config['PERMANENT_SESSION_LIFETIME'] = settings.PERMANENT_SESSION_LIFETIME
    
    # Register blueprints
    app.register_blueprint(auth)
    app.register_blueprint(routes)
    
    # Main route
    @app.route('/')
    def index():
        return render_template('index.html')
    
    # Log configuration once
    logger.info("Flask Configuration:")
    logger.info(f"Secret key set: {bool(app.secret_key)}")
    logger.info(f"Upload folder: {app.config['UPLOAD_FOLDER']}")
    logger.info(f"Session cookie secure: {app.config['SESSION_COOKIE_SECURE']}")
    
    return app

# Create the application instance
app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)
