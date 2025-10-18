import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging

from src.logger import get_logger, setup_logger


def pytest_configure():
    setup_logger("test")
    get_logger().setLevel(logging.DEBUG)
