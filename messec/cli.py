"""Server commands: `flask --app messec:create_app create-admin` and `seed-demo`."""

import click

from .extensions import db
from .models import ADMIN, APPROVED, Station, User, utcnow
from .services import audit
from .validation import check_email, check_password

# Invented locations for demos only. Never load these on a real deployment.
DEMO_STATIONS = [
    ("Eko Command", "police", 6.5244, 3.3792, "Lagos Island"),
    ("Adeola Odeku Post", "police", 6.4531, 3.3958, "Victoria Island"),
    ("Alausa Base", "police", 6.5855, 3.3210, "Ikeja"),
    ("Gberefu Outpost", "police", 6.4698, 3.2378, "Badagry"),
    ("Oko-Oba Station", "fire", 6.6018, 3.3515, "Agege"),
    ("Ojuelegba Unit", "ambulance", 6.5355, 3.4125, "Surulere"),
    ("Ikate Patrol", "hospital", 6.4481, 3.4723, "Lekki"),
    ("Igando Guard", "fire", 6.5721, 3.2960, "Alimosho"),
]


def register_commands(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt="Full name")
    @click.password_option()
    def create_admin(email, name, password):
        """Create an admin account."""
        email, error = check_email(email)
        error = error or check_password(password)
        if error:
            raise click.ClickException(error)
        user = User(email=email, full_name=name.strip(), role=ADMIN)
        user.set_password(password)
        db.session.add(user)
        audit.record("user.created", None, f"admin {email} (command line)")
        db.session.commit()
        click.echo(f"Admin {email} created.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Load invented Lagos stations for demos. Not for real deployments."""
        added = 0
        for name, kind, lat, lng, zone in DEMO_STATIONS:
            if Station.query.filter_by(name=name).first():
                continue
            db.session.add(Station(
                name=name, type=kind, status=APPROVED, zone=zone, address=f"{zone} (demo)", lat=lat, lng=lng,
                phone="0800 000 0000", review_note="Demo data", reviewed_at=utcnow(),
            ))
            added += 1
        db.session.commit()
        click.echo(f"Added {added} demo stations.")
