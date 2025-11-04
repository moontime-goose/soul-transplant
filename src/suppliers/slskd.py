import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from os import path
from typing import Any, Iterable, Optional

import slskd_api
from ratelimit import limits

from src.app import LIB_LOGGER_NAME
from src.file_match import FilelistMatch
from src.file_supplier import FileSupplier, SearchFailedException
from src.model import Album, Filelist, FilelistEntry
from src.search import make_search_strings
from src.soul_config import Config
from src.utils import sleep_and_retry

logger = logging.getLogger(LIB_LOGGER_NAME)


class SlskdApi(FileSupplier):
    slskd: slskd_api.SlskdClient
    config: Config

    def __init__(self, config: Config):
        host = f"{config.soulseek_client.host}:{config.soulseek_client.port}"
        api_key = config.soulseek_client.api_key
        self.slskd = slskd_api.SlskdClient(host, api_key)
        self.config = config
        self.lock = threading.Lock()

    def search_album(self, album: Album) -> Iterable[Filelist]:
        thread_pool = ThreadPoolExecutor(max_workers=8)
        search_strings = make_search_strings(album)

        # Cast a wide net. Generic queries will work of less popular stuff, where
        # number of files in responses comes under souiseek server limit (which
        # comes at under 300 users and 2000-5000 files found, as configured currently).
        supplier_results = [
            thread_pool.submit(lambda s=s: list(self.search_text(s))) for s in search_strings
        ]

        # If this occurs, then more specific queries might help. I haven't found any
        # documentation on soulseek query syntax (if any), so try searching for
        # folder names instead.

        for supplier_result_fut in as_completed(supplier_results):
            filelists = supplier_result_fut.result()
            for fl in filelists:
                yield fl

    def search_text(self, search_str: str) -> Iterable[Filelist]:
        MAX_SLEEP_MS = 15000
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
                if (
                    "Complete" in state["state"]
                    or "Fail" in state["state"]
                    or "Error" in state["state"]
                ):
                    break
            time.sleep(CHECK_INTERVAL_MS / 1000.0)

        states = state["state"].split(", ")

        if "Fail" in state["state"] or "Error" in state["state"]:
            raise SearchFailedException

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

        logger.debug(
            "Soulseek search '%s' completed (%d responses) state: %s",
            state["searchText"],
            len(responses),
            states,
        )

        # Randomize, but prefer uploads with at least 1Mb/s up speed
        responses.sort(
            key=lambda r: r["uploadSpeed"] * (5 if r["hasFreeUploadSlot"] else 1), reverse=True
        )
        responses = map(parse_slskd_response, responses)

        for response in responses:
            yield response

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

    def is_downloadable(self, folder_match: FilelistMatch) -> bool:
        # Sanity check - slskd does not allow specifying download path, so
        # there's no control over what directories are going to be created.
        # Check that there are no conflicts
        target_folder = folder_match.download_list.folder_name
        reference_folder = folder_match.reference_list.folder_name
        target_path = path.join(self.config.staging_folder, target_folder)
        if path.exists(target_path):
            logger.info(
                "MATCH: '%s' skip: target folder already exists",
                target_folder,
            )
            return False

        reference_path = path.join(self.config.staging_folder, reference_folder)
        if path.exists(reference_path):
            logger.info(
                "MATCH: '%s' skip: reference folder '%s' already exists, avoid rename conflict",
                target_folder,
                reference_folder,
            )
            return False

        has_nested_folders = any("/" in entry.name for entry in folder_match.reference_list.files)

        if has_nested_folders:
            logger.info(
                "MATCH: '%s' skip: slskdcannot download nested folders",
                target_folder,
            )
            return False

        return True

    def format_list_oneline(self, filelist: Filelist) -> str:
        return f"{filelist.folder_name} from user [black on blue]{filelist.meta['username']}[/]"

    def search(self, query: str, timeout_ms=15000) -> dict:
        return self.lookup_completed_search(query) or self.start_search(query, timeout_ms)

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
    @limits(calls=1, period=4)
    @limits(calls=10, period=60)
    @limits(calls=25, period=200)
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
