"""App factory for uvicorn --reload mode.

uvicorn needs an importable `app` object. This module builds it from env
config so that ``uvicorn mycelium.server.app_factory:app --reload`` works.
"""

from mycelium.server.cli import build_config_from_env
from mycelium.server.app import create_app

app = create_app(build_config_from_env())
