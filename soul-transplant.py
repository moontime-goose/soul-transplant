from src.logger import *

setup_logger("soul-transplant")

import argparse
import os
import os.path
import signal
import sys
import time

import qbittorrentapi
import rich.prompt as prompt
import torf
import yaml
from qbittorrentapi import TorrentState
from rich import print
from yaml.parser import ParserError

import src.app as app
import src.gazelle_api as gazelle_api
import src.soul_config as soul_config
from src.model import FilelistEntry
from src.shard import Shard
from src.soul_config import Config, TorrentClient
from src.utils import *

# Get logger instance
logger = get_logger()


def signal_handler(sig, frame):
    logger.info("Received interrupt signal, shutting down gracefully...")
    sys.exit(0)


def make_parser():
    parser = argparse.ArgumentParser("gather-shards")

    parser.add_argument("album_folders", nargs="+")
    parser.add_argument(
        "--log",
        nargs="?",
        help="Logging level for the application",
        choices=["debug", "info", "warning", "error", "critical"],
        default="info",
        dest="loglevel",
    )

    parser.add_argument(
        "-c",
        "--config",
        nargs="?",
        help="Path to configuration file",
        dest="config_path",
        default=None,
    )

    parser.add_argument(
        "--log-dev",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Enable source locations, and logging for some of the libraries. ",
    )

    return parser


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = make_parser()
    args = parser.parse_args()
    if args.log_dev:
        logger.addHandler(get_handler(log_dev=True))
        logging.getLogger("urllib3").setLevel(args.log_dev.upper())
        logging.getLogger("urllib3").addHandler(get_handler(log_dev=True))
        logging.getLogger("requests").setLevel(args.log_dev.upper())
        logging.getLogger("requests").addHandler(get_handler(log_dev=True))
        logging.getLogger("requests_cache").setLevel(args.log_dev.upper())
        logging.getLogger("requests_cache").addHandler(get_handler(log_dev=True))
    else:
        logger.addHandler(get_handler(log_dev=False))

    logger.setLevel(args.loglevel.upper())
    try:
        config = Config(**soul_config.make_config(args))
    except FileNotFoundError:
        print("Config file not found, exit")
        sys.exit(1)

    download_dir = config.staging_folder
    shards = [os.path.join(d, app.SHARD_FILE_BASENAME) for d in args.album_folders]

    logger.info("Got %d shards", len(shards))
    torrent_paths: list[str] = []
    for shard_path in shards:
        logger.debug("Reading %s", shard_path)
        if not os.path.exists(shard_path):
            logger.warning("No shards in %s, skip", shard_path)
            continue

        with open(shard_path) as f:
            try:
                shard = Shard.model_validate(yaml.safe_load(f))
            except ParserError as e:
                logger.warning("Skip malformed shard at %s: %s", shard_path, e)
                continue

        shard_dirname = os.path.dirname(shard_path)
        if not (
            is_download_complete(shard_dirname, shard)
            and folder_structure_restored(shard_dirname, shard)
        ):
            logger.info("Skipping %s: download is not complete or is invalid", shard_path)
            continue

        shard_catalog = shard.catalog_ids[0]
        catalog_config = next(
            (catalog for catalog in config.catalogs if catalog.id == shard_catalog.catalog_id),
            None,
        )

        if catalog_config is None:
            raise ValueError(f"Catalog {shard_catalog.catalog_id} is not configured")

        if catalog_config.type == "Gazelle":
            tracker = gazelle_api.Tracker(
                catalog_config.url.encoded_string(), catalog_config.api_key
            )
            torrent_file_path = cache_path(f"{shard_catalog.download_id}.torrent")
            if not os.path.exists(torrent_file_path):
                logger.info(
                    "Fetching torrent id=%s to %s",
                    shard_catalog.download_id,
                    torrent_file_path,
                )
                tracker.download_torrent(shard_catalog.download_id, torrent_file_path)
            torrent_paths.append(torrent_file_path)
        else:
            raise ValueError(f"Catalog type {catalog_config.type} is not supported")

    qbit_config = config.torrent_clients[0]
    qbit_client = qbittorrentapi.Client(
        host=qbit_config.host,
        port=qbit_config.port,
        username=qbit_config.username,
        password=qbit_config.password,
    )

    hashes = {}
    for path in torrent_paths:
        try:
            t = torf.Torrent.read(path)
            hashes[t.infohash] = path
        except torf.TorfError as e:
            # These are downloaded, not created. If the torrent file from the
            # tracker is bad somehow, there may be bigger issues, bail altogeter
            logger.error("Bail on error reading torrent file %s: %s", path, e)
            exit(1)

    logger.info("Got %d possible torrents to add", len(torrent_paths))

    existing_torrents = qbit_client.torrents_info(torrent_hashes=hashes)
    existing_hashes = [info["hash"] for info in existing_torrents]
    new_torrents = dict()

    for infohash, path in hashes.items():
        if infohash in existing_hashes:
            logger.info("%s already exists in qbit", infohash)
            continue
        new_torrents[infohash] = path

    logger.info("Got %d new torrents to add", len(new_torrents))
    if not new_torrents:
        sys.exit(0)

    save_path = download_dir
    if qbit_config.prefix_mapping:
        save_path = os.path.join(
            qbit_config.prefix_mapping.remote,
            os.path.relpath(download_dir, qbit_config.prefix_mapping.host),
        )

    ret = qbit_client.torrents_add(
        torrent_files=new_torrents.values(),
        save_path=save_path,
        is_skip_checking=False,
        use_auto_torrent_management=False,
        is_stopped=True,
        use_download_path=False,
    )

    logger.info("qbittorrent torrent add return %s", ret)

    if ret != "Ok.":
        logger.error("Qbittorrent failed(?) to add torrents. exit")
        sys.exit(1)

    ALLOWED_STATES = [
        TorrentState.PAUSED_DOWNLOAD,
        TorrentState.PAUSED_UPLOAD,
        TorrentState.STOPPED_DOWNLOAD,
        TorrentState.STOPPED_UPLOAD,
    ]
    for _ in range(5):
        logger.info("Wait for torrents to be added")
        time.sleep(1)
        added_torrents = qbit_client.torrents_info(torrent_hashes=new_torrents.keys())
        if len(added_torrents) != len(new_torrents):
            continue

        errored_torrent = next(
            filter(lambda t: t["state"] == TorrentState.ERROR, added_torrents), None
        )
        if errored_torrent:
            logger.error("Error from the added torrent: %s", errored_torrent.name)

        if all(t["state"] in ALLOWED_STATES for t in added_torrents):
            break

    qbit_client.torrents_recheck(new_torrents.keys())

    print("Torrent recheck triggered, check qBittorrent interface")

    qbit_client.auth_log_out()


def get_full_path(shard: Shard, name):
    return os.path.join(shard.reference_folder, name)


def is_download_complete(download_folder, shard: Shard):
    files = shard.files

    prompt_to_confirm = False
    for entry in files:
        download_name = entry.download_name
        reference_name = entry.reference_name
        reference_size = entry.reference_size

        src = os.path.join(download_folder, download_name)
        dst = os.path.join(download_folder, reference_name)
        if not (os.path.exists(src) or os.path.exists(dst)):
            logger.warning("Missing: [yellow]%s[/] or [yellow]%s[/]", download_name, reference_name)
            prompt_to_confirm = True
            continue

        existing_file = src if os.path.exists(src) else dst
        size = os.path.getsize(existing_file)
        if os.path.getsize(existing_file) != reference_size:
            logger.warning(
                "Suspicious file size for %s: %d, but expected", existing_file, size, reference_size
            )
            return False

    return (not prompt_to_confirm) or prompt.Confirm.ask("Match this folder?")


def folder_structure_restored(download_folder, shard: Shard):
    files = shard.files

    for entry in files:
        download_name = entry.download_name
        reference_name = entry.reference_name

        if download_name == reference_name:
            continue

        logger.debug("%s -> %s", download_name, reference_name)
        src = os.path.join(download_folder, download_name)
        dst = os.path.join(download_folder, reference_name)

        assert src != dst

        # TODO Case-insensitive file systems will goof here and file will not be
        # renamed. Find a way to check for this
        if not os.path.exists(dst):
            if not os.path.exists(src):
                raise FileNotFoundError(src)
            if not prompt.Confirm.ask(
                f"Old: {download_name}\nNew: {reference_name}\nRename?", default=True
            ):
                return False

            os.rename(src, dst)

    if download_folder != shard.reference_folder:
        src = download_folder
        dst = shard.reference_folder

        if os.path.exists(dst):
            logger.warning("Folder with the original name already exists, skip")
            return False
        if not prompt.Confirm.ask(f"Old: {src}\nNew: {dst}\nRename?", default=True):
            return False

        os.rename(src, dst)

    return True


if __name__ == "__main__":
    main()
