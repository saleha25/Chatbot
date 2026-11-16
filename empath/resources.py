"""Practical Help page: verified helplines and simple steps for IDPs.

A separate page. It does not touch the chat or any other feature."""
from flask import render_template

from .core import app, login_required


@app.route("/resources")
@login_required
def resources():
    return render_template("resources.html")