import logging
import os
import time
from functools import wraps

from ratelimit import RateLimitException
from rich.prompt import Confirm
from xdg_base_dirs import xdg_cache_home, xdg_config_home

import src.app as app
from src.soul_config import Config

logger = app.get_logger()


def flatten(xss):
    return (x for xs in xss for x in xs)


def prompt_yes_no(
    config: Config, prompt: str, default=False, force_user=False, log_auto=None
) -> bool:
    """
    Wrapper around click.confirm to account for user provided settings, like
    --timid and --assume-yes|no (TBD)
    """

    prompt_base = prompt.rstrip().rstrip("?")
    if force_user or config.timid:
        return Confirm.ask(f">>> {prompt_base}?", default=default, show_default=True)
    else:
        if log_auto:
            logger.log(log_auto, "%s", prompt_base)
        return default


def config_path(path) -> str:
    """Return path to a file in application config folder."""
    ensure_directory_exists(xdg_config_home())
    return os.path.join(xdg_config_home(), "soul-transplant", path)


def cache_path(path) -> str:
    """Return path to a file in application cache folder."""
    ensure_directory_exists(xdg_cache_home())
    return os.path.join(xdg_cache_home(), "soul-transplant", path)


def ensure_directory_exists(path):
    """Ensure directory exists, creating it if necessary."""
    os.makedirs(path, exist_ok=True)


class SleepAndRetryDecorator(object):
    """
    Reimplementation of sleep_and_retry decoration, but with a log message
    """

    def __init__(self, limiter_name, log_level=logging.DEBUG, min_logged_sleep_sec=5):
        self.limiter_name = limiter_name
        self.log_level = log_level
        self.min_logged_sleep_min_sec = min_logged_sleep_sec

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            while True:
                try:
                    return func(*args, **kwargs)
                except RateLimitException as e:
                    if e.period_remaining > self.min_logged_sleep_min_sec:
                        logger.log(
                            self.log_level,
                            "%s rate limit hit, sleep for %.2f seconds",
                            self.limiter_name,
                            e.period_remaining,
                        )
                    time.sleep(e.period_remaining)

        return wrapper


sleep_and_retry = SleepAndRetryDecorator
