import random
import threading
import time
from collections import defaultdict
from typing import Any, Optional

import slskd_api
from ratelimit import limits, sleep_and_retry
from yaml import parse

from src.file_match import FilelistMatch
from src.file_supplier import FileSupplier
from src.logger import *
from src.model import Filelist, FilelistEntry
from src.response_cache import *
from src.soul_config import Config
from src.utils import *

logger = get_logger()


class SlskdApi(FileSupplier):
    slskd: slskd_api.SlskdClient

    def __init__(self, config: Config):
        host = f"{config.soulseek_client.host}:{config.soulseek_client.port}"
        api_key = config.soulseek_client.api_key
        self.slskd = slskd_api.SlskdClient(host, api_key)
        self.config = config
        self.lock = threading.Lock()

    def perform_search(self, search_str: str) -> tuple[FileSupplier.SearchStatus, list[Filelist]]:
        MAX_SLEEP_MS = 20000
        CHECK_INTERVAL_MS = 100

        with self.lock:
            search_info = self.search(search_str, timeout_ms=15000)

        search_id = search_info["id"]

        state = {"state": "InProgress"}
        for _ in range(int(MAX_SLEEP_MS / CHECK_INTERVAL_MS)):
            with self.lock:
                state = self.slskd.searches.state(search_id)
                # TODO: state string is ", ".join()'ed list of states. Notable ones are
                # "Completed", "InProgress", "ResponseLimitReached". Latter one could be
                # considered to decide on whether searches should be
                # repeated/rephrased/etc
                if "Complete" in state["state"]:
                    break
            time.sleep(CHECK_INTERVAL_MS / 1000.0)

        states = state["state"].split(", ")

        ret_state = (
            FileSupplier.SearchStatus.LIMIT_REACHED
            if "ResponseLimitReached" in states
            else FileSupplier.SearchStatus.COMPLETE
        )

        with self.lock:
            responses = self.slskd.searches.search_responses(search_id)
            if ret_state == FileSupplier.SearchStatus.LIMIT_REACHED and len(responses) == 0:
                logger.debug(
                    "Soulseek search '%s': limit reached, but 0 responses, wait and retry",
                    state["searchText"],
                )
                time.sleep(2)
                responses = self.slskd.searches.search_responses(search_id)

        logger.info(
            "Soulseek search '%s' completed with %d user responses",
            state["searchText"],
            len(responses),
        )
        logger.debug("Soulseek search '%s' completed with state: %s", state["searchText"], states)

        responses = triage_responses(responses)
        responses = map(parse_slskd_response, responses)

        return (ret_state, list(responses))

    def search(self, query: str, timeout_ms=15000) -> dict:
        return self.lookup_completed_search(query) or self.start_search(query, timeout_ms)

    def enqueue_download(self, filelist: Filelist) -> tuple[FileSupplier.DownloadStatus, Any]:
        slskd_filelist = [f.meta["file"] for f in filelist.files]
        responses = [self.slskd.transfers.enqueue(filelist.meta["username"], slskd_filelist)]
        all_succeeded = all(responses)
        status = (
            FileSupplier.DownloadStatus.SCHEDULED
            if all_succeeded
            else FileSupplier.DownloadStatus.FAILED
        )
        return (status, all_succeeded)

    def lookup_completed_search(self, query: str) -> Optional[dict]:
        searches = self.slskd.searches.get_all()
        found = next((s for s in searches if s["searchText"] == query), None)
        if found:
            logger.debug("Reuse slskd search for : %s", query)
        return found

    def wait_for_completion(self, search_id: str) -> tuple[list[str], list[dict]]:
        MAX_SLEEP_MS = 20000
        CHECK_INTERVAL_MS = 10

        state = {}
        for _ in range(int(MAX_SLEEP_MS / CHECK_INTERVAL_MS)):
            state = self.slskd.searches.state(search_id)
            # TODO: state string is ", ".join()'ed list of states. Notable ones are
            # "Completed", "InProgress", "ResponseLimitReached". Latter one could be
            # considered to decide on whether searches should be
            # repeated/rephrased/etc
            if "Complete" in state["state"]:
                break
            time.sleep(CHECK_INTERVAL_MS / 1000.0)

        logger.debug("Soulseek search %s completed with status %s", search_id, state["state"])

        states = state["state"].split(", ")
        responses = self.slskd.searches.search_responses(search_id)

        return (states, responses)

    @sleep_and_retry("slskd", log_level=logging.INFO, min_logged_sleep_sec=5)
    @limits(calls=25, period=200)
    @limits(calls=1, period=4)
    def start_search(self, query: str, timeout_ms=10000) -> dict:
        """
        Kick off a search with given text query
        """
        logger.debug("Search slskd for       : %s", query)

        return self.slskd.searches.search_text(
            query, filterResponses=True, searchTimeout=timeout_ms, responseLimit=300
        )


def parse_slskd_response(response: dict) -> Filelist:
    files = [
        FilelistEntry(
            name=entry["filename"].replace("\\", "/"),
            size=entry["size"],
            meta={"file": entry},
        )
        for entry in response["files"]
    ]

    filelist = Filelist(folder_name=".", files=files, meta={"username": response["username"]})
    return filelist


def triage_responses(responses) -> list[dict]:
    """
    Rearrange responses in terms of likelihood of success (ignoring the
    actual file listings), like upload speed/slots
    """
    responses = list(filter(lambda r: r["hasFreeUploadSlot"], responses))

    # Randomize, but prefer uploads with at least 1Mb/s up speed
    random.shuffle(responses)
    responses.sort(key=lambda r: r["uploadSpeed"] > 1048576, reverse=True)

    return responses
