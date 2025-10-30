"""
Application logging setup and configuration
"""

# I started this whole thing intending to re-use logging system in multiple main
# python scripts, (i.e. avoiding passing logger name to get_logger() functions
# called in each file) but now it looks weird somehow, and I couldn't enable
import logging

import rich.logging

from src.app import LIB_LOGGER_NAME


def setup_logger(log_dev=False):
    logger = logging.getLogger(LIB_LOGGER_NAME)
    logger.addHandler(get_handler(log_dev))
    return logger


def get_handler(log_dev=False):
    if log_dev:
        return rich.logging.RichHandler(show_time=False, markup=True, show_path=True)
    else:
        return rich.logging.RichHandler(show_time=False, markup=True, show_path=False)
