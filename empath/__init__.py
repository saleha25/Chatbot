"""Empath AI backend, split into modules. Importing this package registers every route."""

from .core import app  # noqa: F401

# Importing the modules registers their @app.route functions
from . import database  # noqa: F401,E402
from . import assessment  # noqa: F401,E402
from . import context  # noqa: F401,E402
from . import text_utils  # noqa: F401,E402
from . import safety  # noqa: F401,E402
from . import language  # noqa: F401,E402
from . import responses  # noqa: F401,E402
from . import auth_routes  # noqa: F401,E402
from . import chat_routes  # noqa: F401,E402
from . import pages  # noqa: F401,E402
from . import words_of_hope  # noqa: F401,E402
from . import password_reset  # noqa: F401,E402