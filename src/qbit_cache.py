import datetime
import logging
from functools import cache
from typing import Optional

import qbittorrentapi

from src.app import LIB_LOGGER_NAME
from src.model import Filelist
from src.soul_config import Config

logger = logging.getLogger(LIB_LOGGER_NAME)


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class QbitCache(metaclass=Singleton):
    client: qbittorrentapi.Client
    cache: Optional[qbittorrentapi.TorrentInfoList]
    infohash_map: dict[str, qbittorrentapi.TorrentDictionary]
    name_map: dict[str, qbittorrentapi.TorrentDictionary]
    cache_time: datetime.datetime

    def __init__(self, config: Config):
        qbit_config = config.torrent_clients[0]
        self.client = qbittorrentapi.Client(
            host=qbit_config.host,
            port=qbit_config.port,
            username=qbit_config.username,
            password=qbit_config.password,
            REQUESTS_ARGS={"timeout": (3.1, 30)},
        )
        self.cache = None
        self.infohash_map = dict()
        self.name_map = dict()
        self.cache_time = datetime.datetime(1970, 1, 1)

    def torrent_exists(self, fl: Filelist):
        self.ensure_cache()
        infohash = None
        if details := fl.meta.get("details", None):
            infohash = details.torrent.info_hash

        folder_name = fl.folder_name

        suspiciously_similar_torrent = (
            infohash and self.infohash_map.get(infohash.upper(), None)
        ) or self.name_map.get(folder_name, None)

        if suspiciously_similar_torrent is not None:
            logger.debug("%s - already exists in qbit, hash %s", folder_name, infohash)
            return True

        return False

    def ensure_cache(self):
        if self.cache and (datetime.datetime.now() - self.cache_time) < datetime.timedelta(
            seconds=180
        ):
            return

        self.cache = self.client.torrents_info()
        self.infohash_map = {str(info["hash"]): info for info in self.cache}
        self.name_map = {str(info["name"]): info for info in self.cache}

        self.cache_time = datetime.datetime.now()
