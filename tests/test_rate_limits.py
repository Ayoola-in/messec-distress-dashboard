"""Rate limits are off in the other tests, so check here that they really engage."""

from messec import create_app
from messec.extensions import db


def test_login_attempts_are_limited(tmp_path):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'limits.db'}",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": True,
    })
    with app.app_context():
        db.create_all()
    client = app.test_client()
    codes = [client.post("/login", data={"email": "a@b.co", "password": "wrong"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    with app.app_context():
        db.engine.dispose()
