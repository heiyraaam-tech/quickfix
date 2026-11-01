import click
from flask import current_app

from .extensions import db
from .models import User
from .seed import DEMO_PASSWORD, seed_catalogue, seed_demo
from .services import recurring
from .validators import password_problem, valid_email


def register_cli(app):
    @app.cli.command("seed-catalogue")
    def seed_catalogue_cmd():
        """Load Pune localities and the service catalogue (no users). Safe to re-run."""
        click.echo(f"Added {seed_catalogue()} catalogue rows.")

    @app.cli.command("seed-demo")
    def seed_demo_cmd():
        """Load DEMO customer/professional accounts (development only)."""
        if current_app.config["ENV_NAME"] == "production":
            raise click.ClickException("Refusing to load demo data in production.")
        made = seed_demo()
        click.echo("Demo accounts created: " + (", ".join(made) or "none (already present)"))
        click.echo(f"Demo password for all demo accounts: {DEMO_PASSWORD}  (development only!)")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt=True)
    @click.password_option()
    def create_admin(email, name, password):
        """Create an administrator. Admins can never be created from the website."""
        email = email.strip().lower()
        if not valid_email(email):
            raise click.ClickException("That email address looks invalid.")
        if (msg := password_problem(password)):
            raise click.ClickException(msg)
        if len(password) < 10:
            raise click.ClickException("Admin passwords need at least 10 characters.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException("A user with that email already exists.")
        u = User(email=email, name=name.strip(), role="admin")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Administrator {email} created.")

    @app.cli.command("generate-visits")
    def generate_visits_cmd():
        """Create any missing visits for active recurring schedules (idempotent). Run daily if you like."""
        click.echo(f"Created {recurring.generate_all()} visits.")
