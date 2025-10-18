import logging
from collections import defaultdict

from src.logger import get_handler, setup_logger

setup_logger("soul-snatch")

import argparse
import json
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from os import path
from typing import Iterable, Optional

import click
import requests
import requests_cache
import yaml
from rich import print
from yaml.parser import ParserError

import src.app as app
import src.gazelle_api as gazelle_api
import src.shard as shard
import src.soul_config as soul_config
import src.suppliers.soulseek as soulseek
from src.catalogs.gazelle_catalog import GazelleCatalog
from src.file_catalog import FileCatalog
from src.file_match import (
    FilelistMatch,
    attempt_filelist_match,
    prompt_match_confirmation,
)
from src.file_supplier import FileSupplier
from src.model import Album, Filelist
from src.search import make_search_strings, normalize_query
from src.soul_config import CatalogConfig, Config
from src.utils import *

logger = app.get_logger()


def signal_handler(sig, frame):
    print()
    logger.warning("Received interrupt signal, shutting down gracefully...")
    sys.exit(0)


def make_parser():
    """
    Application parser configuration
    """
    parser = argparse.ArgumentParser(
        prog="soul-transplant",
        description="Search soulseek network for cross-seedable music albums",
    )

    parser.add_argument(
        "-f",
        "--file",
        required=True,
        dest="input_file",
        help="File with the list of albums to look up",
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
        "--timid",
        action="store_true",
        default=False,
        help="""
        Ask for user confirmation on every interaction.
        Default: queries autoconfirm, downloads/deletions and
        potentially destructive actions will require confirmation
        """,
    )

    parser.add_argument(
        "--check-infohash",
        action="store_true",
        default=True,
        help="""
        (Default) Check qbittorrent for matching infohash.
        Excludes redundant downloads, increases number of queries to tracker(s) from this script
        """,
    )

    parser.add_argument(
        "--no-check-infohash",
        action="store_false",
        dest="check_infohash",
        help="""
        Don't check torrent client for matching infohashes.
        Possible redundant downloads, but less requests to tracker
        """,
    )

    parser.add_argument(
        "--search-folder-names",
        action="store_true",
        default=False,
        help="""
        Search for albums by the original folder names known from tracker.
        May result in a lot of queries to soulseek and hitting ites rate limits
        """,
    )

    parser.add_argument(
        "--log-dev",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Enable source locations, and logging for some of the libraries. ",
    )

    parser.add_argument(
        "--cache-expire-after",
        default=(7 * 24 * 60 * 60),
        help="Number of seconds after which responses from tracker and slskd are cached",
    )

    return parser


def merge_config_arguments(config_data, args):
    """
    Resolve configuration overrides and finalize the config for this run
    """
    arg_values = vars(args)
    for parameter in ["timid", "check_infohash", "search_folder_names"]:
        if parameter in arg_values:
            config_data[parameter] = arg_values[parameter]

    return Config(**config_data)


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = make_parser()
    args = parser.parse_args()
    logger.setLevel(args.loglevel.upper())
    try:
        config = merge_config_arguments(soul_config.make_config(args), args)
    except FileNotFoundError:
        print("Config file not found, exit")
        sys.exit(1)

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

    if len(config.catalogs) > 1:
        logger.warning("Multiple catalogs are not yet supported, using the first one")

    # Cache get requests from catalog(s)
    urls_expire_after = {
        f"{config.catalogs[0]}/ajax.php*": args.cache_expire_after,
        "*": requests_cache.DO_NOT_CACHE,
    }

    requests_cache.install_cache(
        backend="sqlite", urls_expire_after=urls_expire_after, serializer="json"
    )

    tracker_catalog = make_catalog(config, config.catalogs[0])
    slskd_supplier = soulseek.SlskdApi(config)

    albums = parse_albumlist(args.input_file)
    for album in albums:
        process_album_search(config, tracker_catalog, slskd_supplier, album)


def process_album_search(
    config: Config, catalog: FileCatalog, supplier: FileSupplier, album: Album
):
    """
    Main function for handling a single album
    """

    # This whole function is begging to be parallelized or async-awaited, but
    # it's python, so this can wait. Plus, even as it is, soulseek already needs
    # to be rate-limited for this to work. So, leave it for now, and enjoy a
    # nice readable linear log

    # Visual separation before starting new album search
    print("\n")
    if not prompt_yes_no(
        config, f"Search for album: {album}?", default=True, log_auto=None, force_user=True
    ):
        return

    print(f"{album}: start search")

    catalog_results = catalog.search(album)

    if prompt_yes_no(
        config, f"{album}: edit {len(catalog_results)} results?", default=False, force_user=True
    ):
        catalog_results = select_results(config, catalog, catalog_results)

    # Pre-check whether target directory already exists. This might change after
    # some downloads are approved.
    # t_candidates = list(reject_directory_conflicts(t_candidates, config.staging_folder))

    if not catalog_results:
        logger.info("%s: no tracker matches found", album)
        return

    # Cast a wide net. Generic queries will work of less popular stuff, where
    # number of files in responses comes under souiseek server limit (which
    # comes at around 100-200 users and 2000-5000 files found).

    logger.info("%s: search soulseek for %d candidates", album, len(catalog_results))

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda s: supplier.perform_search(s), make_search_strings(album))
        )

        results = [(catalog_card, result) for catalog_card in catalog_results for result in results]

    # If this occurs, then more specific queries might help. I haven't found any
    # documentation on soulseek query syntax (if any), so try searching for
    # folder names instead.
    is_response_limit_reached = False

    done_list = []
    retry_list = []
    for catalog_card, (state, filelists) in results:
        if state == FileSupplier.SearchStatus.LIMIT_REACHED:
            is_response_limit_reached = True

        if target_folder_exists(config.staging_folder, catalog_card):
            logger.debug("Target folder '%s' already exists, skip", catalog_card.folder_name)
            continue

        username = process_search(config, album, catalog, supplier, catalog_card, filelists)

        if username is not None:
            logger.info(
                "[black on blue]%s[/], thanks! %s found", username, catalog_card.folder_name
            )
            done_list.append(catalog_card)
        else:
            retry_list.append(catalog_card)

    # Maybe, try search for folder names specifically. This is no silver bullet,
    # soulseek has been adding extra stuff even if query uses quotes to try
    # searching for exact phrase, and for popular old stuff you can easily have
    # north of 20 different folder names to look up. Searches below will be
    # rate-limited often

    if not should_search_by_folder_names(config, is_response_limit_reached, album, len(retry_list)):
        logger.info("Matched %d torrent to soulseek", len(done_list))
        # Done with this album
        return

    # More specific searched. No ideas other than folder names at the moment

    retry_list = [
        catalog_card
        for catalog_card in retry_list
        if not target_folder_exists(config.staging_folder, catalog_card)
    ]

    folder_map: defaultdict[str, list[Filelist]] = defaultdict(list)

    for catalog_card in retry_list:
        folder_map[catalog_card.folder_name].append(catalog_card)

    logger.info("Matched %d torrents, try %d folder name searches", len(done_list), len(folder_map))

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = executor.map(
            lambda item: (
                item[1],
                supplier.perform_search(normalize_query(item[0])),
            ),
            folder_map.items(),
        )

    # Handle completion and results for folder searches
    for catalog_cards, (_, filelists) in results:
        for catalog_card in catalog_cards:
            username = process_search(config, album, catalog, supplier, catalog_card, filelists)
            if username is not None:
                logger.info(
                    "[black on blue]%s[/], thanks! %s found", username, catalog_card.folder_name
                )
                done_list.append(catalog_card)
                break

    logger.info("Matched %d total torrents to soulseek", len(done_list))


def process_search(
    config: Config,
    album: Album,
    catalog: FileCatalog,
    supplier: FileSupplier,
    reference_list: Filelist,
    filelists: list[Filelist],
) -> Optional[str]:
    """
    Decide whether files from given responses can be used, and enqueue a (1)
    download if there are matching files.
    """

    download_username = None

    for filelist in filelists:
        folder_match = attempt_filelist_match(
            album, filelist, reference_list, media_format=config.media_format
        )
        if folder_match is None:
            continue

        link = catalog.format_meta_link(reference_list)
        logger.info(
            "[on green]MATCH[/]: %s from [black on blue]%s[/]", link, filelist.meta["username"]
        )

        # Doing infohash check is somewhat dumb: application could look up infohash on
        # tracker ahead of time (<1 second) and avoid soulseek search (>5
        # seconds). For the time being I picked extra work for soulseek. Also,
        # it might work out for the better, because soulseek might return a few
        # more responses with differently phrased and more specific queries.
        #
        # It this goes far enough to port to asyncio or an alternative,
        # infohashes can be easily checked while soulseek searches are being
        # executed

        download_dir = path.join(config.staging_folder, folder_match.suggested_folder)
        if os.path.exists(download_dir):
            logger.info(
                "[on green]MATCH[/]: '%s' skip: target folder already exists",
                folder_match.suggested_folder,
            )
            continue

        download_dir = path.join(config.staging_folder, folder_match.reference_list.folder_name)
        if os.path.exists(download_dir):
            logger.info(
                "[on green]MATCH[/]: '%s' skip: reference folder already exists",
                folder_match.suggested_folder,
            )
            continue

        if catalog.already_exists(folder_match.reference_list):
            logger.warning(
                "[on green]MATCH[/]: '%s' skip: torrent already in qbit", reference_list.folder_name
            )
            break

        if not prompt_match_confirmation(
            config,
            folder_match,
            f"[on green]MATCH[/]: '{folder_match.suggested_folder}': from user [black on blue]{filelist.meta['username']}[/]. Accept?",
        ):
            continue

        try:
            shard_path = drop_shard(config, catalog, folder_match)
        except FileExistsError:
            logger.info(
                "[on green]MATCH[/]: '%s' skip: target directory %s already exists",
                reference_list.folder_name,
            )
            continue

        try:
            status, _ = supplier.enqueue_download(folder_match.download_list)
            if status == FileSupplier.DownloadStatus.SCHEDULED:
                download_username = filelist.meta["username"]
                break
        except requests.HTTPError as e:
            # This is not necessarily an error, large amount of files enqueued
            # from the same user might result in slskd instance, or remote
            # user's soulseek client hitting one of its limits (like number of
            # upload slots, fiels enqueued, or transfer limit). If slskd is
            # running, all those files are likely enqueued, even if with an
            # error status, and slskd seems to handle retries in this cases
            logger.warning("slskd returned an error, check the UI: %s", e)
            if not prompt_yes_no(
                config, f"Continue search for folder {reference_list.folder_name}?", default=True
            ):
                break

    return download_username


def target_folder_exists(base_dir: str, filelist: Filelist) -> bool:
    return os.path.exists(os.path.join(base_dir, filelist.folder_name))


def reject_directory_conflicts(
    filelists: Iterable[Filelist], download_dir: str
) -> Iterable[Filelist]:
    """
    Skip torrents with directories, which already exist in target download
    directory.
    """

    # Intended to avoid overwrites and merges of downloaded albums (possible
    # with popular naming schemes)

    return (fl for fl in filelists if not target_folder_exists(download_dir, fl))


def drop_shard(config: Config, catalog: FileCatalog, download_match: FilelistMatch) -> str:
    """
    Leave additional information for tools that will process completed
    downloads, like gather-shards.

    Current implementation is simplified for single flat folder downloads, or at
    least folders where all the music is located in the root folder of the album.

    Returns path to created shard file.
    """

    # This might actually be substituted with a full origin.yaml file, at least
    # for single folder downloads, or after slskd gets support for nested folder
    # downloads. That said, there's no real need, since the folder will end up
    # in torrent client, where gazelle-origin can be configured.

    download_dirname = download_match.suggested_folder
    download_dir = path.join(config.staging_folder, download_dirname)

    os.mkdir(download_dir)

    shard_path = os.path.join(download_dir, app.SHARD_FILE_BASENAME)
    with open(shard_path, mode="w") as shard_file:
        files = [
            shard.FileDownload(
                download_name=path.basename(file_match.suggested.name),
                reference_name=file_match.reference.name,
                reference_size=file_match.reference.size,
            )
            for file_match in download_match.files
        ]
        catalog_id = catalog.make_catalog_download_id(download_match.reference_list)
        reference_folder = os.path.join(
            config.staging_folder, download_match.reference_list.folder_name
        )
        new_shard = shard.Shard(
            catalog_ids=[catalog_id], files=files, reference_folder=reference_folder
        )

        yaml.safe_dump(new_shard.model_dump(), shard_file)

    return shard_path


def parse_albumlist(filename):
    """
    Parse input file with list of album information. At the moment, it
    expect json with an array of objects with the following keys:

    - album
    - albumartist
    - original_year

    """

    l = json.load(open(filename))
    return [Album.model_validate(a) for a in l]


def select_results(config: Config, catalog, catalog_results: list[Filelist]) -> list[Filelist]:
    try:
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
    except ParserError as e:
        logger.error("Error parsing edited catalog results: %s", e)
        logger.error("Continue with original results")

        return catalog_results


def should_search_by_folder_names(
    config: Config, is_response_limit_reached: bool, album: Album, folder_count: int
) -> bool:
    if config.search_folder_names:
        return True
    elif is_response_limit_reached:
        prompt = f"Hit response limit. Search soulseek for {album} by {folder_count} folder names?"
        return prompt_yes_no(config, prompt, default=True)

    return False


def make_catalog(config: Config, catalog: CatalogConfig) -> FileCatalog:
    if catalog.type == "Gazelle":
        tracker = gazelle_api.Tracker(catalog.url.encoded_string(), catalog.api_key)
        return GazelleCatalog(config, catalog, tracker)
    else:
        raise NotImplementedError


if __name__ == "__main__":
    main()
