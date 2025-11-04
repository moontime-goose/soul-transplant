import abc
import logging
from typing import Any, Iterable, Iterator

import click
import qbittorrentapi

import src.soul_shard as soul_shard
from src.app import LIB_LOGGER_NAME
from src.model import Album, Filelist
from src.soul_config import Config
from src.utils import prompt_yes_no

logger = logging.getLogger(LIB_LOGGER_NAME)


class FileCatalog(abc.ABC):
    """
    Abstraction for any source from where file listings can be obtained.
    """

    @abc.abstractmethod
    def search(self, album: Album) -> list[Filelist]:
        raise NotImplementedError

    @abc.abstractmethod
    def fill_meta(self, result: Filelist) -> Filelist:
        raise NotImplementedError

    @abc.abstractmethod
    def format_meta_link(self, filelist: Filelist) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def format_meta_download_id(self, filelist: Filelist) -> Any:
        raise NotImplementedError

    @abc.abstractmethod
    def make_catalog_download_id(self, filelist: Filelist) -> soul_shard.CatalogDownloadId:
        raise NotImplementedError


def maybe_prompt_select_results(
    config: Config, album: Album, catalog, catalog_results: list[Filelist]
) -> list[Filelist]:
    if not prompt_yes_no(
        config,
        f"{album}: edit {len(catalog_results)} results?",
        default=False,
        force_user=not config.confident,
    ):
        return catalog_results

    logger.debug("Editing list of %d candidates", len(catalog_results))
    lines = "\n".join(
        f"{i:4}\t{catalog.format_meta_link(fl):80} {fl.folder_name}"
        for i, fl in enumerate(catalog_results)
    )
    edited_list = click.edit(lines)
    if edited_list is not None:
        lines = edited_list.split("\n")
        indices = [int(s.strip().split("\t")[0].strip()) for s in lines if s]
        edited_results = [r for i, r in enumerate(catalog_results) if i in indices]
        logger.debug("Edited to %d candidates", len(catalog_results))

        return edited_results
    else:
        return catalog_results


def get_catalog_results(config: Config, album: Album, catalog: FileCatalog) -> Iterator[Filelist]:
    catalog_results = catalog.search(album)

    if not catalog_results:
        return iter([])

    catalog_results = maybe_prompt_select_results(config, album, catalog, catalog_results)

    logger.info("%s: getting full catalog information for %d results", album, len(catalog_results))

    if config.check_infohash:
        catalog_results = map(lambda result: catalog.fill_meta(result), catalog_results)

    catalog_results = reject_if_torrent_exists(config, catalog_results)

    if not catalog_results:
        return iter([])

    return catalog_results


def reject_if_torrent_exists(config: Config, filelists: Iterable[Filelist]) -> Iterator[Filelist]:
    qbit_config = config.torrent_clients[0]
    qbit_client = qbittorrentapi.Client(
        host=qbit_config.host,
        port=qbit_config.port,
        username=qbit_config.username,
        password=qbit_config.password,
    )

    qbit_torrents = qbit_client.torrents_info()
    infohash_map = {info["hash"]: info for info in qbit_torrents}
    name_map = {info["name"]: info for info in qbit_torrents}

    def torrent_exists(fl: Filelist):
        infohash = None
        if details := fl.meta.get("details", None):
            infohash = details.torrent.info_hash

        folder_name = fl.folder_name

        suspiciously_similar_torrent = infohash_map.get(infohash, None) or name_map.get(
            folder_name, None
        )

        if suspiciously_similar_torrent is not None:
            logger.debug("%s - already exists in qbit, hash %s", folder_name, infohash)
            return True

        return False

    return filter(lambda fl: not torrent_exists(fl), filelists)
