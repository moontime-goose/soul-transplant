import os
import sys
from os import path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, HttpUrl
from xdg_base_dirs import xdg_config_home
from yaml.parser import ParserError

import src.app as app

logger = app.get_logger()


class CatalogConfig(BaseModel):
    id: str
    url: HttpUrl
    type: str = Field(..., pattern="^(Gazelle)$")
    api_key: str = Field(exclude=True)


class PrefixMapping(BaseModel):
    host: str
    remote: str


class SoulseekClient(BaseModel):
    type: str = Field(..., pattern="^(slskd)$")
    host: str
    port: int = Field(..., ge=1, le=65535)
    api_key: str = Field(exclude=True)


class TorrentClient(BaseModel):
    type: str = Field(..., pattern="^(qBittorrent)")
    host: str
    port: int = Field(..., ge=1, le=65535)
    username: str = Field(exclude=True)
    password: str = Field(exclude=True)
    prefix_mapping: Optional[PrefixMapping]


class Config(BaseModel):
    """Configuration model for soul-transplant tools"""

    config_version: str = Field(..., pattern="^0.1$")

    staging_folder: str
    # Soulseek configuration
    soulseek_client: SoulseekClient

    torrent_clients: list[TorrentClient] = Field(..., min_length=1, max_length=1)

    # Media format filter
    media_format: Optional[str] = Field(default=None, pattern="^(FLAC|MP3)$")
    media_encoding: Optional[str] = None

    # Catalog configuration
    catalogs: List[CatalogConfig] = Field(..., min_length=1, max_length=1)

    # Optional configuration with defaults
    max_cache_age_minutes: int = Field(default=4320, ge=0)
    allow_trumpable: bool = False
    timid: bool = False
    check_infohash: bool = True
    search_folder_names: bool = False
    cache_expire_after: int = Field(default=7 * 24 * 60 * 60, ge=0)  # 7 days in seconds


def make_config(args=None) -> dict:
    config_path = None if args is None else args.config_path
    config_path = find_config(config_path)
    if not path.exists(config_path):
        logger.error("config file not found: %s", config_path)
        exit(1)

    try:
        config_data = read_config(config_path)
    except ParserError as e:
        logger.error("Failed to parse config %s: %s", config_path, e)
        exit(1)

    return config_data


def read_config(config_path: os.PathLike) -> dict:
    return yaml.safe_load(open(config_path))


def find_config(suggested_path: Optional[os.PathLike]) -> os.PathLike:
    """
    Find an existing config file, either in user-provided or one of the
    pre-configured locations
    """

    def lookup_paths(suggested):
        """
        Return list of paths to look for config in
        """
        if suggested:
            return [suggested]
        else:
            script_dir = path.dirname(path.abspath(sys.argv[0]))
            return [
                path.join(script_dir, "config.yaml"),  # Script dir
                xdg_config_home() / "soul-transplant/config.yaml",  # XDG config dir
            ]

    paths = lookup_paths(suggested_path)
    found_path = next(filter(path.exists, paths), None)
    if found_path is None:
        raise FileNotFoundError("Config file not found")

    return found_path
