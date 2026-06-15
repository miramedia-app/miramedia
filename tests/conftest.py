"""Shared test setup.

Environment must be set before any ``miramedia`` import: config and logging
initialise at import time (see ``miramedia/config.py:36`` and the Makefile
``openapi`` target, which sets ``MIRAMEDIA_LOG_FILE`` for the same reason).
"""

import os

os.environ.setdefault("MIRAMEDIA_LOG_FILE", "/dev/null")
