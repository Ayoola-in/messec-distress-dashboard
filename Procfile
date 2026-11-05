web: flask --app messec:create_app db upgrade && gunicorn -w 1 --threads 100 -b 0.0.0.0:$PORT wsgi:app
