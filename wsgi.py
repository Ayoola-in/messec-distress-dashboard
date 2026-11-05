"""Production entry point. Run with exactly one worker, because live updates and the
auto-assign sweep live in this process:

    gunicorn -w 1 --threads 100 wsgi:app
"""

from messec import create_app, start_background_tasks

app = create_app()
start_background_tasks(app)
