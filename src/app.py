import os
import sys
from os import path
from typing import Optional

import yaml
from xdg_base_dirs import xdg_config_home
from yaml.parser import ParserError

import src.logger

# re-export
get_logger = src.logger.get_logger

SHARD_FILE_BASENAME = "soul-shard.yaml"
