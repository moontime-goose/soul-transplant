"""
Application logging setup and configuration
"""

# I started this whole thing intending to re-use logging system in multiple main
# python scripts, (i.e. avoiding passing logger name to get_logger() functions
# called in each file) but now it looks weird somehow, and I couldn't enable
import logging

import rich.logging

_logger = None


def setup_logger(name):
    global _logger

    if _logger is not None:
        return

    logger = logging.getLogger(name)
    _logger = logger


def get_handler(log_dev=False):
    if log_dev:
        return rich.logging.RichHandler(show_time=False, markup=True, show_path=True)
    else:
        return rich.logging.RichHandler(show_time=False, markup=True, show_path=False)


def get_logger() -> logging.Logger:
    global _logger
    assert _logger is not None
    return _logger
