import argparse
import logging
import os
import random
import signal
import sys
import time
from collections import defaultdict, namedtuple
from functools import reduce

import qbittorrentapi
import requests
from requests import request
from rich import print
from rich.progress import track

import src.gazelle_api as gazelle_api
import src.logger as soul_logger
import src.soul_config as soul_config
from src.catalogs.gazelle_catalog import GazelleCatalog
from src.file_match import attempt_filelist_match, format_match
from src.logger import get_handler
from src.model import Album, Filelist, TorrentDetails
from src.soul_config import Config
from src.torrent import verify_torrent
from src.utils import cache_path

logger = logging.getLogger("soul-search")


class TrackerInfo:
    torrent: qbittorrentapi.TorrentDictionary
    trackers: qbittorrentapi.TrackersList
    files: qbittorrentapi.TorrentFilesList

    def __init__(self, torrent):
        self.torrent = torrent
        self.trackers = torrent.trackers
        self.files = torrent.files


def signal_handler(sig, frame):
    print()
    logger.warning("Received interrupt signal, shutting down gracefully...")
    sys.exit(0)


def make_parser():
    """
    Application parser configuration
    """
    parser = argparse.ArgumentParser(
        prog="soul-search",
        description="Search torrent client for cross-seedable music albums",
    )

    parser.add_argument(
        "--log",
        choices=["debug", "info", "warning", "error", "critical"],
        default="info",
        dest="loglevel",
        help="Logging level for the application",
    )

    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        help="""
        Path to configuration file.
        Default: config will we search for in script and XDG config directories
        """,
    )

    parser.add_argument(
        "--log-dev",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Enable source locations, and logging for some of the libraries. ",
    )

    return parser


def merge_config_arguments(config_data, args):
    """
    Resolve configuration overrides and finalize the config for this run
    """

    return Config(**config_data)


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = make_parser()
    args = parser.parse_args()

    try:
        config = merge_config_arguments(soul_config.make_config(args), args)
    except FileNotFoundError:
        print("Config file not found, exit")
        sys.exit(1)

    logger.setLevel(args.loglevel.upper())
    logger.addHandler(get_handler(args.log_dev))

    lib_logger = soul_logger.setup_logger(args.log_dev)
    lib_logger.setLevel(args.loglevel.upper())

    if args.log_dev:
        logging.getLogger("urllib3").setLevel(args.log_dev.upper())
        logging.getLogger("urllib3").addHandler(get_handler(log_dev=True))

        logging.getLogger("requests").setLevel(args.log_dev.upper())
        logging.getLogger("requests").addHandler(get_handler(log_dev=True))

        logging.getLogger("requests_cache").setLevel(args.log_dev.upper())
        logging.getLogger("requests_cache").addHandler(get_handler(log_dev=True))

    if len(config.catalogs) <= 1:
        logger.error("Need at least two catalogs to work")
        sys.exit(1)

    assert all(catalog.type == "Gazelle" for catalog in config.catalogs)

    catalogs: dict[str, GazelleCatalog] = {
        catalog.tracker_url.encoded_string(): GazelleCatalog(
            config,
            catalog,
            gazelle_api.Tracker(config, catalog.url.encoded_string(), catalog.api_key),
        )
        for catalog in config.catalogs
    }
    tracker_urls = [catalog.tracker_url.encoded_string() for catalog in config.catalogs]

    qbit_config = config.torrent_clients[0]
    qbit_client = qbittorrentapi.Client(
        host=qbit_config.host,
        port=qbit_config.port,
        username=qbit_config.username,
        password=qbit_config.password,
    )

    existing_torrents = qbit_client.torrents_info(sort="added_on", reverse=False)
    existing_torrent_map: dict[str, qbittorrentapi.TorrentDictionary] = {
        str(t["hash"]).upper(): t
        for t in track(existing_torrents, description="Getting torrent tracker list")
    }
    torrents_by_tracker: defaultdict[str, list[qbittorrentapi.TorrentDictionary]] = defaultdict(
        list
    )

    for _, t in track(existing_torrent_map.items(), "Gropuing torrents by tracker"):
        for catalog_tracker_url in tracker_urls:
            if any(catalog_tracker_url in tracker.url for tracker in t.trackers):
                if t["progress"] == 1.0:
                    torrents_by_tracker[catalog_tracker_url].append(t)
                break

    torrent_file_map = defaultdict(list)
    file_torrent_map = defaultdict(list)
    for torrent in track(
        reduce(lambda l1, l2: l1 + l2, torrents_by_tracker.values()), "Tracking down files"
    ):
        infohash = torrent["hash"]
        file_paths = [
            os.path.join(str(torrent["save_path"]).replace("/data/", "/mnt/media_nfs/"), f.name)
            for f in torrent.files
        ]
        inodes = [os.stat(path).st_ino for path in file_paths]
        torrent_file_map[infohash].extend(inodes)
        for ino in inodes:
            file_torrent_map[ino].append(infohash)
    time.sleep(15)

    def is_cross_seeded(t):
        inodes = torrent_file_map[t["hash"]]
        for ino in inodes:
            if len(file_torrent_map[ino]) > 1:
                return True

        return False

    for tracker_url, torrents in sorted(torrents_by_tracker.items())[:1]:
        logger.info("Try to cross-seed %d torrents from tracker %s", len(torrents), tracker_url)
        src_catalog = catalogs[tracker_url]
        dst_catalogs = [tracker for (url, tracker) in catalogs.items() if url != tracker_url]

        torrents = torrents[:]
        random.shuffle(torrents)
        for i, torrent in enumerate(
            track(
                torrents,
                description="Searching for cross-seeds",
            )
        ):
            try:
                print("\nindex: ", i)
                if is_cross_seeded(torrent):
                    logger.info("already cross-seeded, skip")
                    time.sleep(1)
                    continue
                infohash = str(torrent["hash"])
                content_path = str(torrent["content_path"])
                if qbit_config.prefix_mapping:
                    content_path = content_path.replace(
                        qbit_config.prefix_mapping.remote, qbit_config.prefix_mapping.host
                    )
                logger.info("Torrent files at %s", content_path)
                src_details = src_catalog.tracker.get_torrent_details(infohash=infohash)

                album = Album(
                    artist=src_details.group.music_info["artists"][0]["name"],
                    name=src_details.group.name,
                    year=src_details.group.year,
                )
                logger.info("Will search for the album: %s", album)
                reference_list = Filelist(
                    folder_name=src_details.torrent.file_path, files=src_details.torrent.file_list
                )
                for catalog in dst_catalogs:
                    try:
                        suggestions = catalog.search(album)
                        for suggested_list in suggestions:
                            dst_details: TorrentDetails = suggested_list.meta["details"]
                            list_match = attempt_filelist_match(
                                album,
                                suggested_list,
                                reference_list,
                                suggested_folder=dst_details.torrent.file_path,
                            )
                            if list_match is None:
                                continue
                            if list_match.suggested_folder != list_match.reference_list.folder_name:
                                logger.warning(
                                    "Renamed folder: '%s' != '%s'",
                                    list_match.suggested_folder,
                                    list_match.reference_list.folder_name,
                                )
                                continue

                            if list_match.overall_similarity() != 100:
                                print(format_match(list_match))
                                logger.warning("Something different, skip")
                                continue

                            logger.info("Found match for %s", album)

                            suggested_list = catalog.fill_meta(suggested_list)
                            dst_details: TorrentDetails = suggested_list.meta["details"]

                            if (dst_details.torrent.info_hash is not None) and (
                                dst_details.torrent.info_hash.upper() in existing_torrent_map.keys()
                            ):
                                logger.info("Already exists in client, continue")
                                continue

                            if is_torrent_file_already_downloaded(catalog, dst_details):
                                logger.info("Torrent file already downloaded, skip")
                                continue

                            try:
                                torrent_file_path = get_torrent_file_path(catalog, dst_details)
                            except Exception:
                                time.sleep(15)
                                continue

                            percentage_valid, size = verify_torrent(torrent_file_path, content_path)
                            if percentage_valid < 100:
                                logger.warning(
                                    "Torrent content mismatch: %.2f%% valid", percentage_valid
                                )
                                continue

                            ret = qbit_client.torrents_add(
                                torrent_files=[torrent_file_path],
                                save_path=str(torrent["save_path"]),
                                is_skip_checking=False,
                                use_auto_torrent_management=False,
                                use_download_path=False,
                            )

                            if ret != "Ok.":
                                logger.error("Qbittorrent failed(?) to add torrents, ret: %s", ret)
                    except gazelle_api.Tracker.StatusError:
                        time.sleep(15)

            except requests.exceptions.ConnectionError as e:
                logger.error("exception: %s", e)
                time.sleep(15)
                continue


def is_torrent_file_already_downloaded(catalog: GazelleCatalog, t: TorrentDetails) -> bool:
    torrent_file_path = cache_path(f"{catalog.catalog.id}-{t.torrent.id}.torrent")
    return os.path.exists(torrent_file_path)


def get_torrent_file_path(catalog: GazelleCatalog, t: TorrentDetails) -> str:
    torrent_file_path = cache_path(f"{catalog.catalog.id}-{t.torrent.id}.torrent")
    if not os.path.exists(torrent_file_path):
        logger.info(
            "Fetching torrent id=%s to %s",
            t.torrent.id,
            torrent_file_path,
        )
        catalog.tracker.download_torrent(t.torrent.id, torrent_file_path)
    return torrent_file_path


if __name__ == "__main__":
    main()
