import logging

from src.logger import get_handler, setup_logger

setup_logger("soul-snatch")

import slskd_api

import src.soul_config as soul_config


def main():
    config = soul_config.Config(**soul_config.make_config())

    slskd = slskd_api.SlskdClient(
        f"{config.soulseek_client.host}:{config.soulseek_client.port}",
        config.soulseek_client.api_key,
    )
    searches = slskd.searches.get_all()
    for s in searches:
        slskd.searches.delete(s["id"])


if __name__ == "__main__":
    main()
