"""
Logic related to selecting torrent upload applicable for soulseek search,
based on contents, media format, user preferences, and whatnot.
"""

import logging
from typing import Any, Iterable

import src.gazelle_api as gazelle_api
import src.soul_shard as soul_shard
from src.app import LIB_LOGGER_NAME
from src.file_catalog import FileCatalog
from src.model import Album, Filelist, SearchResult, TorrentDetails
from src.search import normalize_query
from src.soul_config import CatalogConfig, Config
from src.utils import flatten, prompt_yes_no

logger = logging.getLogger(LIB_LOGGER_NAME)


class GazelleCatalog(FileCatalog):
    catalog: CatalogConfig
    tracker: gazelle_api.Tracker
    config: Config

    def __init__(self, config: Config, catalog: CatalogConfig, tracker: gazelle_api.Tracker):
        self.catalog = catalog
        self.config = config
        self.tracker = tracker

    def search(self, album: Album) -> list[Filelist]:
        """
        Get torrents which match given album
        """

        candidates = search_tracker_candidates(
            self.config,
            self.catalog,
            self.tracker,
            album,
            self.config.media_format,
            self.config.media_encoding,
        )

        filelists = [
            Filelist(
                folder_name=t.torrent.file_path,
                files=t.torrent.file_list,
                meta={"catalog": self.catalog.id, "details": t},
            )
            for t in candidates
        ]

        return filelists

    def fill_meta(self, result: Filelist) -> Filelist:
        details: TorrentDetails = result.meta["details"]
        full_details = self.tracker.get_torrent_details(details.torrent.id)

        new_result = result.model_copy()
        new_result.meta["details"] = full_details

        return new_result

    def format_meta_link(self, filelist: Filelist) -> str:
        return self.tracker.format_torrent_link(filelist.meta["details"].torrent.id)

    def format_meta_download_id(self, filelist: Filelist) -> Any:
        return {
            "type": "Gazelle",
            "catalog": self.catalog.id,
            "id": filelist.meta["details"].torrent.id,
        }

    def make_catalog_download_id(self, filelist: Filelist) -> soul_shard.CatalogDownloadId:
        return soul_shard.CatalogDownloadId(
            catalog_id=self.catalog.id,
            download_id=filelist.meta["details"].torrent.id,
            type=self.catalog.type,
        )


def search_tracker_candidates(
    config: Config,
    catalog: CatalogConfig,
    tracker: gazelle_api.Tracker,
    album: Album,
    media_format,
    media_encoding,
) -> Iterable[TorrentDetails]:
    """
    Get and pre-filter torrent candidates for given albums
    """

    logger.info("Searching %s for %s - %s", catalog.id, album.artist, album.name)

    # Get group details for given search result group id
    def get_group_torrents(result):
        return tracker.get_group_details(result.group_id)

    # Check torrent search result, using 'mandatory' attributes (the ones that
    # are returned from every endpoint returning torrent information)
    def precheck_torrent_result(details: TorrentDetails):
        return (
            (not media_format or details.torrent.format == media_format)
            and (not media_encoding or details.torrent.encoding == media_encoding)
            and (config.allow_trumpable or (not details.torrent.trumpable))
        )

    search_results = tracker.search_album_group(album, media_format=media_format)

    # Ensure the right artist, album, version, etc
    search_results = filter(
        lambda result: is_group_applicable(config, result, album), search_results
    )

    # Individual torrent results
    group_results = map(get_group_torrents, search_results)

    torrent_results = (
        (TorrentDetails(group=g.group, torrent=t) for t in g.torrents) for g in group_results
    )

    torrent_results = flatten(torrent_results)

    # Pre-check - after getting group torrents in bulk results should be filtered again
    torrent_results = filter(precheck_torrent_result, torrent_results)

    return torrent_results


def is_group_applicable(config: Config, result: SearchResult, album: Album) -> bool:
    """Check if search result matches what application asked for."""

    # Very rough, could do some normalization like replacing apostrophes,
    # quotes, etc. with whitespace
    return normalize_query(result.artist.lower()) == normalize_query(
        album.artist.lower()
    ) and normalize_query(result.group_name.lower()) == normalize_query(album.name.lower())


def user_confirm_group(config: Config, candidate: TorrentDetails, link=None):
    return prompt_yes_no(
        config, f"Accept group   {link or candidate.group.id}?", default=True, log_auto=logging.INFO
    )


def user_confirm_torrent(config: Config, candidate: TorrentDetails, link=None):
    return prompt_yes_no(
        config,
        f"Accept torrent {link or candidate.torrent.id} : {candidate.torrent.file_path}?",
        default=True,
        log_auto=logging.DEBUG,
    )
