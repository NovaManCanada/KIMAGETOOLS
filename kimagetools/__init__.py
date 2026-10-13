"""KImageTools — a small Flask app for optimizing a single image."""

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from . import cleanup
from .config import Config


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    app.logger.setLevel(app.config["LOG_LEVEL"])

    if app.config["TRUSTED_PROXIES"]:
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n)

    # Limits come from the RATELIMIT_* config keys.
    app.extensions["limiter_instance"] = Limiter(
        get_remote_address,
        app=app,
        application_limits_exempt_when=lambda: request.endpoint == "static",
    )

    cleanup.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    max_mb = app.config["MAX_UPLOAD_MB"]

    @app.errorhandler(413)
    def too_large(_err):
        return jsonify(error=f"File is too large. The limit is {max_mb} MB."), 413

    @app.errorhandler(429)
    def rate_limited(err):
        return jsonify(error=f"Too many requests (limit: {err.description}). "
                             "Please wait a minute and try again."), 429

    return app
