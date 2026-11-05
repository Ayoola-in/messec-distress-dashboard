"""Run MesSec locally: `python app.py`. Applies database migrations, then starts the server."""

import os

from dotenv import load_dotenv
from flask_migrate import upgrade

from messec import create_app, start_background_tasks
from messec.extensions import socketio
from messec.models import ADMIN, User

load_dotenv()
app = create_app()

if __name__ == "__main__":
    with app.app_context():
        upgrade()
        if not User.query.filter_by(role=ADMIN).first():
            print("[MesSec] No admin yet. Create one with: flask --app messec:create_app create-admin")
    start_background_tasks(app)
    if app.config["MESSEC_SIMULATE"]:
        print("[MesSec] DEMO MODE: simulated signals are ON (set MESSEC_SIMULATE=0 for real use)")
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False,
                 allow_unsafe_werkzeug=True)
