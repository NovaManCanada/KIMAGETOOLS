"""Entry point: `flask --app app run` for development, `gunicorn app:app` for production."""

import os

from kimagetools import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
