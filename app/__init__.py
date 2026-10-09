# D:\iitm_scheduler\app\__init__.py
from flask import Flask
from app.config import Config
from app.extensions import db
from app.auth import auth_bp
from app.main import main_bp
from app.api import api_bp
from flask import Flask, request

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    # ── Make term dates available in all templates ──
    @app.context_processor
    def inject_term_dates():
        return {
            'default_term_start': app.config['DEFAULT_TERM_START'],
            'default_term_end': app.config['DEFAULT_TERM_END'],
            'default_term_label': app.config['DEFAULT_TERM_LABEL'],
            'google_client_id': app.config.get('GOOGLE_CLIENT_ID', ''),
        }

    # ── Prevent caching of authenticated pages ──
    # Without this, browsers serve the cached dashboard HTML on "Back"
    # after logout, bypassing @login_required entirely.
    @app.after_request
    def add_no_cache_headers(response):
        # Skip static assets (CSS/JS/images) — allow normal caching
        if '/static/' in request.path:
            return response
        # Skip API endpoints that may use their own caching
        if request.path.startswith('/api/'):
            return response

        response.headers['Cache-Control'] = (
            'no-store, no-cache, must-revalidate, max-age=0, proxy-revalidate'
        )
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    with app.app_context():
        db.create_all()

    return app