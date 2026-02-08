import logging
import os
import time
from functools import wraps

from ratelimit import RateLimitException
from rich.progress import track
from rich.prompt import Confirm
from platformdirs import user_cache_dir, user_config_dir

from src.app import LIB_LOGGER_NAME

logger = logging.getLogger(LIB_LOGGER_NAME)


def flatten(xss):
    return (x for xs in xss for x in xs)


def prompt_yes_no(
    config, prompt: str, default=False, force_user=False, log_auto=None
) -> bool:
    """
    Wrapper around click.confirm to account for user provided settings, like
    --timid and --assume-yes|no (TBD)
    """

    prompt_base = prompt.rstrip().rstrip("?")
    if not config.unattended and force_user or config.timid:
        return Confirm.ask(f">>> {prompt_base}?", default=default, show_default=True)
    else:
        if log_auto:
            logger.log(log_auto, "%s", prompt_base)
        return default


def config_path(path) -> str:
    """Return path to a file in application config folder, creating base folders as needed. """
    config_dir = user_config_dir("soul-transplant", ensure_exists=True)
    ret =  os.path.join(config_dir, path)
    ensure_directory_exists(os.path.dirname(ret))
    return ret


def cache_path(path) -> str:
    """Return path to a file in application cache folder, creating base folders as needed."""
    cache_dir = user_cache_dir("soul-transplant", ensure_exists=True)
    ret =  os.path.join(cache_dir, path)
    ensure_directory_exists(os.path.dirname(ret))
    return ret


def ensure_directory_exists(path):
    """Ensure directory exists, creating it if necessary."""
    os.makedirs(path, exist_ok=True)
    return path


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


def to_percentage(x: float) -> int:
    return int(round(x * 100))


def maybe_progress_bar(iterable, config, **kwargs):
    if config.unattended:
        return track(iterable, **kwargs)
    else:
        return iterable
