import abc
import logging
from typing import Any, Iterable, Iterator

import click
import qbittorrentapi

import src.soul_shard as soul_shard
from src.app import LIB_LOGGER_NAME
from src.model import Album, Filelist
from src.qbit_cache import QbitCache
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
        force_user=not config.unattended,
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

    if config.check_infohash:
        logger.info(
            "%s: getting full catalog information for %d results", album, len(catalog_results)
        )
        catalog_results = map(lambda result: catalog.fill_meta(result), catalog_results)

    catalog_results = reject_if_torrent_exists(config, catalog_results)

    if not catalog_results:
        return iter([])

    return iter(catalog_results)


_qbit_cache = None


def reject_if_torrent_exists(config: Config, filelists: Iterable[Filelist]) -> Iterator[Filelist]:
    global _qbit_cache
    if not _qbit_cache:
        _qbit_cache = QbitCache(config)

    assert _qbit_cache is not None
    return filter(lambda fl: not _qbit_cache.torrent_exists(fl), filelists)
