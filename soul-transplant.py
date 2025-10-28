from concurrent.futures import CancelledError
from typing import Optional

from click.termui import progressbar

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
from rich.progress import Progress, Task, TaskID
from yaml.parser import ParserError

import src.app as app
import src.gazelle_api as gazelle_api
import src.soul_config as soul_config
from src.model import FilelistEntry
from src.soul_config import Config, TorrentClient
from src.soul_shard import Shard
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

    torrent_file_paths: dict[str, Shard] = dict()

    logger.info("Got %d shard files", len(shards))
    for shard_path in shards:
        print()

        try:
            shard = arrange_torrent_content(config, shard_path)

            torrent_file_paths[get_torrent_file_path(config, shard)] = shard
        except FileNotFoundError as e:
            logger.warning("%s: could not repair torrent contents, skip: %s", shard_path, e)
            continue
        except IsADirectoryError as e:
            logger.warning("%s: target directory already exists, skip", shard_path)
        except CancelledError as e:
            logger.info("%s: cancelled repair, skip", shard_path)
            continue
        except ValueError as e:
            logger.warning("%s: %s, skip", shard_path, e)

    qbit_config = config.torrent_clients[0]
    qbit_client = qbittorrentapi.Client(
        host=qbit_config.host,
        port=qbit_config.port,
        username=qbit_config.username,
        password=qbit_config.password,
    )

    existing_torrents = qbit_client.torrents_info()
    existing_hashes = {info["hash"]: info for info in existing_torrents}

    new_torrents: dict[str, str] = dict()

    for path, shard in torrent_file_paths.items():
        t = torf.Torrent.read(path)
        if t.infohash in existing_hashes:
            logger.info("'%s' already exists in qbit", existing_hashes[t.infohash]["name"])
            continue

        percentage_valid, size = verify_torrent(t, shard)
        logger.info(
            "%s: torrent is %.02f%% complete, %.02f MB left to download",
            path,
            percentage_valid,
            (size * percentage_valid / 100) / 1048576,
        )
        if percentage_valid == 100 or prompt.Confirm.ask("Submit to qBittorrent?"):
            new_torrents[t.infohash] = path

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


def ensure_download_complete(config: Config, download_folder: str, shard: Shard):
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

    if prompt_to_confirm and (
        config.skip_incomplete_downloads or not prompt.Confirm.ask("Match this folder?")
    ):
        raise FileNotFoundError(f"Missing files in {download_folder}")


def ensure_torrent_content_structure(download_folder, shard: Shard):
    files = shard.files

    rename_arguments: list[tuple[str, str]] = []
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

            if src != dst and src.lower() == dst.lower():
                rename_arguments.append((src, f"{src}.bkp"))
                rename_arguments.append((f"{src}.bkp", dst))
            else:
                rename_arguments.append((src, dst))

    if download_folder != shard.reference_folder:
        src = download_folder + "/"
        dst = shard.reference_folder + "/"

        if os.path.exists(dst):
            logger.warning("Folder with the original name already exists, skip")
            raise IsADirectoryError(dst)

        rename_arguments.append((src, dst))

    if rename_arguments:
        logger.info("Queued rename operations:")
        for src, dst in rename_arguments:
            logger.info("%-36s -> %-36s", os.path.basename(src), os.path.basename(dst))

        if not prompt.Confirm.ask("Confirm?"):
            raise CancelledError()

        for src, dst in rename_arguments:
            os.rename(src, dst)
    return True


def arrange_torrent_content(config, shard_path: str) -> Shard:
    with open(shard_path) as f:
        shard = Shard.model_validate(yaml.safe_load(f))

    shard_dirname = os.path.dirname(shard_path)
    ensure_download_complete(config, shard_dirname, shard)
    ensure_torrent_content_structure(shard_dirname, shard)

    return shard


def get_torrent_file_path(config: Config, shard: Shard) -> str:
    shard_catalog = shard.catalog_ids[0]
    catalog_config = next(
        (catalog for catalog in config.catalogs if catalog.id == shard_catalog.catalog_id),
        None,
    )

    if catalog_config is None:
        raise ValueError(f"Catalog {shard_catalog.catalog_id} is not configured")

    if catalog_config.type == "Gazelle":
        tracker = gazelle_api.Tracker(
            config, catalog_config.url.encoded_string(), catalog_config.api_key
        )

        torrent_file_path = cache_path(f"{shard_catalog.download_id}.torrent")
        if not os.path.exists(torrent_file_path):
            logger.info(
                "Fetching torrent id=%s to %s",
                shard_catalog.download_id,
                torrent_file_path,
            )
            tracker.download_torrent(shard_catalog.download_id, torrent_file_path)
        return torrent_file_path
    else:
        raise ValueError(f"Catalog type {catalog_config.type} is not supported")


class TorrentVerifyCallback:
    pieces_valid: int = 0
    pieces_invalid: int = 0
    progress: Progress = Progress()
    task: TaskID

    def __init__(self, name):
        self.progress.start()
        self.task = self.progress.add_task(name)

    def __call__(
        self,
        t: torf.Torrent,
        file: str,
        pieces_checked: int,
        pieces_total: int,
        index: int,
        piece_sha: Optional[bytes],
        exception: Optional[torf.TorfError],
    ):
        if exception is None:
            self.progress.update(self.task, total=pieces_total, advance=1)
            self.pieces_valid += 1
        elif not isinstance(exception, torf.MetainfoError) and not isinstance(
            exception, torf.ReadError
        ):
            raise exception
        else:
            self.progress.update(self.task, total=pieces_total, advance=1)
            self.pieces_invalid += 1

        if index == pieces_total - 1:
            self.progress.stop_task(self.task)


def verify_torrent(t: torf.Torrent, shard: Shard) -> tuple[float, int]:
    try:
        cb = TorrentVerifyCallback(t.name[:40] if t.name else "Progress")
        t.verify(shard.reference_folder, callback=cb, interval=0)

        return (cb.pieces_valid * 100.0 / (cb.pieces_valid + cb.pieces_invalid), t.size)
    except torf.TorfError as e:
        # These are downloaded, not created. If the torrent file from the
        # tracker is bad somehow, there may be bigger issues, bail altogeter
        logger.error("Unexpected error on torrent contente validation: %s", e)
        exit(1)


if __name__ == "__main__":
    main()
