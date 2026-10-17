import os
import sys


if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CommandError(Exception):
    """An external command failed and should be reported without a traceback."""
