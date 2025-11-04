import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging

from src.logger import setup_logger


def pytest_configure():
    lib_logger = setup_logger()
    lib_logger.setLevel(logging.DEBUG)
