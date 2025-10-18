#!/usr/bin/env python3
import argparse
import os
import sys

import qbittorrentapi
import slskd_api
import yaml
from rich import print
from rich.pretty import pprint
from rich.syntax import Syntax

from src.logger import setup_logger

setup_logger("validate-config")

from src.soul_config import Config, find_config, read_config


def main():
    parser = argparse.ArgumentParser(description="Validate soul-transplant config file")
    parser.add_argument("config", nargs="?", help="Path to config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print whole config")
    args = parser.parse_args()

    try:
        config_path = find_config(args.config)
        config_data = read_config(config_path)
        config = Config.model_validate(config_data)

    except Exception as e:
        print(f"Config validation failed: {e}", file=sys.stderr)
        return 1

    if args.verbose:
        pprint(config.model_dump())

    try:
        assert os.path.exists(
            config.staging_folder
        ), f"staging folder does not exist: {config.staging_folder}"
        assert len(config.torrent_clients) == 1, "only one torrent client at a time is supported"

        assert (not config.torrent_clients[0].prefix_mapping) or os.path.exists(
            config.torrent_clients[0].prefix_mapping.host
        ), f"host mapped folder does not exist: {config.torrent_clients[0].prefix_mapping.host}"

        assert (
            config.soulseek_client.api_key
        ), f"Empty slskd api key: {config.soulseek_client.api_key}"

        qbit_config = config.torrent_clients[0]
        qbit_client = qbittorrentapi.Client(
            host=qbit_config.host,
            port=qbit_config.port,
            username=qbit_config.username,
            password=qbit_config.password,
        )

        print("trying to reach qbittorrent")
        assert qbit_client.app_version(), "cannot contact qbittorrent"
        print("qbittorrent reached")

        print("trying to reach slskd")
        host = f"{config.soulseek_client.host}:{config.soulseek_client.port}"
        api_key = config.soulseek_client.api_key
        slskd = slskd_api.SlskdClient(host, api_key)
        print("slskd reached")

        assert slskd.application.version, "cannot contact slskd"

        print("\n[green]Config is valid![/]")
    except Exception as e:
        print(e)
        print("[red]Invalid config! See errors above[/]")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
