import slskd_api
from rich.progress import track

import src.soul_config as soul_config


def main():
    config = soul_config.Config(**soul_config.make_config())

    slskd = slskd_api.SlskdClient(
        f"{config.soulseek_client.host}:{config.soulseek_client.port}",
        config.soulseek_client.api_key,
    )
    searches = slskd.searches.get_all()
    for s in track(searches, description="Clearing search cache..."):
        slskd.searches.delete(s["id"])


if __name__ == "__main__":
    main()
