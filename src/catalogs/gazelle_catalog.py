"""
Logic related to selecting torrent upload applicable for soulseek search,
based on contents, media format, user preferences, and whatnot.
"""

from typing import Any, Iterable

import qbittorrentapi

import src.gazelle_api as gazelle_api
import src.shard as shard
from src.file_catalog import FileCatalog
from src.model import Album, Filelist, SearchResult, TorrentDetails
from src.search import normalize_query
from src.soul_config import CatalogConfig, Config
from src.utils import *

logger = app.get_logger()


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
            self.config, self.tracker, album, self.config.media_format, self.config.media_encoding
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

    def format_meta_link(self, filelist: Filelist) -> str:
        return self.tracker.format_torrent_link(filelist.meta["details"].torrent.id)

    def format_meta_download_id(self, filelist: Filelist) -> Any:
        return {
            "type": "Gazelle",
            "catalog": self.catalog.id,
            "id": filelist.meta["details"].torrent.id,
        }

    def make_catalog_download_id(self, filelist: Filelist) -> shard.CatalogDownloadId:
        return shard.CatalogDownloadId(
            catalog_id=self.catalog.id,
            download_id=filelist.meta["details"].torrent.id,
            type=self.catalog.type,
        )

    def already_exists(self, filelist: Filelist) -> bool:
        if not self.config.check_infohash:
            return False

        qbit_config = self.config.torrent_clients[0]
        qbit_client = qbittorrentapi.Client(
            host=qbit_config.host,
            port=qbit_config.port,
            username=qbit_config.username,
            password=qbit_config.password,
        )

        torrent = filelist.meta["details"].torrent
        if torrent.info_hash is None:
            # Avoid modifying the arguments passed (mostly for my sanity)
            t_candidate = self.tracker.get_torrent_details(torrent.id)
            torrent = t_candidate.torrent
            assert torrent.info_hash is not None

        qbit_torrents = qbit_client.torrents_info()
        suspiciously_similar_torrent = next(
            (
                info
                for info in qbit_torrents
                if info["hash"] == torrent.info_hash or info["name"] == filelist.folder_name
            ),
            None,
        )
        if suspiciously_similar_torrent is not None:
            logger.debug(
                "Similar torrent already exists: hash %s, folder '%s'",
                suspiciously_similar_torrent["hash"],
                suspiciously_similar_torrent["name"],
            )
            return True

        return False


def search_tracker_candidates(
    config: Config, tracker: gazelle_api.Tracker, album: Album, media_format, media_encoding
) -> Iterable[TorrentDetails]:
    """
    Get and pre-filter torrent candidates for given albums
    """

    logger.info("Searching tracker for %s - %s", album.artist, album.name)

    # Get group details for given search result group id
    get_group_torrents = lambda result: tracker.get_group_details(result.group_id)

    # Check torrent search result, using 'mandatory' attributes (the ones that
    # are returned from every endpoint returning torrent information)
    precheck_torrent_result = (
        lambda details: (not media_format or details.torrent.format == media_format)
        and (not media_encoding or details.torrent.encoding == media_encoding)
        and (config.allow_trumpable or (not details.torrent.trumpable))
    )

    ## Pipeline start here

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

    torrent_results = filter(
        lambda result: is_torrent_applicable(
            config,
            result,
            link=tracker.format_torrent_link(result.torrent.id, group_id=result.group.id),
        ),
        torrent_results,
    )

    return torrent_results


def is_group_applicable(config: Config, result: SearchResult, album: Album) -> bool:
    """Check if search result matches what application asked for."""

    # Very rough, could do some normalization like replacing apostrophes,
    # quotes, etc. with whitespace
    return normalize_query(result.artist.lower()) == normalize_query(
        album.artist.lower()
    ) and normalize_query(result.group_name.lower()) == normalize_query(album.name.lower())


def is_torrent_applicable(config: Config, details: TorrentDetails, link=None) -> bool:
    """Check if given torrent can be searched on soulseek."""

    # slskd cannot download nested folders, it flattens the hierarchy instead.
    # Since many folders in music albums are similarly named, like CD1, CD01,
    # Artwork, etc., it's too much trouble to have it working at the moment, at
    # least for general case. Skip them.

    torrent = details.torrent

    is_music_in_subfolders = any(
        "/" in entry.name
        for entry in torrent.file_list
        if (not config.media_format or entry.name.endswith(config.media_format.lower()))
    )

    if is_music_in_subfolders:
        logger.debug(
            "Reject torrent %s : cannot download music files in subfolders", link or torrent.id
        )
        return False

    return True


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
