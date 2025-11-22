import logging
import sqlite3
from typing import Iterable, Optional

import requests as reqs
import requests_cache
from ratelimit import limits

import src.app as app
from src.model import Album, GroupDetails, SearchResult, TorrentDetails
from src.soul_config import Config
from src.utils import sleep_and_retry

logger = logging.getLogger(app.LIB_LOGGER_NAME)


class Tracker:
    """
    Gazelle API. Handles requests-responses between the script and the
    tracker, persisting responses for future reference.
    """

    class StatusError(Exception):
        """
        HTTP code 200, but tracker return error in response body
        """

        def __init__(self, response, message="Tracker return non-success code"):
            self.response = response
            self.message = message
            super().__init__(self.message)

    db_conn: sqlite3.Connection
    tracker_url: str
    tracker_api_key: str
    session: requests_cache.CachedSession

    CACHE_TABLE_NAME = "tracker"

    def __init__(self, config: Config, tracker_url: str, tracker_api_key: str):
        self.tracker_url = tracker_url.rstrip("/")
        self.tracker_api_key = tracker_api_key
        self.session = requests_cache.CachedSession(expire_after=-1)

    def search_album_group(
        self, album: Album, max_pages=3, media_format=None, media_encoding=None
    ) -> Iterable[SearchResult]:
        """
        Search tracker for the album using advanced search features
        (specifically artist and group names) to search for album, as opposed to use
        of searchstr for supposedly full text search
        """
        params = {
            "action": "browse",
            "artistname": album.artist,
            "groupname": album.name,
            "order_by": "snatched",
            "order_way": "desc",
        }

        return self.search_advanced(params, max_pages)

    def search_advanced(self, params, max_pages: int) -> Iterable[SearchResult]:
        """
        Send torrent search request with given parameters, possibly following it
        up with request for subsequent pages. Yield search results one by one.
        """

        def format_query(params):
            """
            Return a string which describes search request specified by params
            in the logs
            """
            if "artistname" in params or "groupname" in params:
                return f"{params['artistname']} - {params['groupname']}"
            else:
                return params["searchstr"]

        current_page = 1
        while True:
            params["page"] = current_page

            body = self.make_json_request(params)

            # may be 0 with no responses
            page_count = body["response"].get("pages", 1)

            # on current pag only
            group_count = len(body["response"]["results"])
            torrent_count = sum(len(group["torrents"]) for group in body["response"]["results"])

            logger.info(
                "Got page %d out of %d (%d group, %d torrents) for %s",
                current_page,
                page_count,
                group_count,
                torrent_count,
                format_query(params),
            )

            for result in body["response"]["results"]:
                yield SearchResult.model_validate(result)

            if current_page >= page_count or current_page >= max_pages:
                break

            current_page += 1

    def get_group_details(self, group_id: int) -> GroupDetails:
        """
        Request group details for given id (notably, file listings for all
        torrents in the group)
        """

        body = self.make_json_request({"action": "torrentgroup", "id": group_id})
        details = GroupDetails.model_validate(body["response"])

        return details

    def get_torrent_details(
        self, id: Optional[int] = None, infohash: Optional[str] = None
    ) -> TorrentDetails:
        """
        Request torrent details for the given id (notably, file listing)
        """

        if (id is None) == (infohash is None):
            raise ValueError("Exactly one of 'id' or 'infohash' must be provided")

        if id is not None:
            body = self.make_json_request({"action": "torrent", "id": id})
        else:
            body = self.make_json_request({"action": "torrent", "hash": infohash})

        details = TorrentDetails.model_validate(body["response"])

        return details

    def download_torrent(self, torrent_id: int, dest_file_path: str):
        """
        Download .torrent file and save it to given file
        """

        request = reqs.Request(
            "GET",
            f"{self.tracker_url}/ajax.php",
            headers={"Authorization": self.tracker_api_key},
            params={"action": "download", "id": torrent_id},
        ).prepare()

        resp = self.send_ratelimited_request(request)

        resp.raise_for_status()

        with open(dest_file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def make_json_request(self, params) -> dict:
        """
        Primary way to make request for mostly static data on the tracker.
        Caches successful responses to avoid repeating request to tracker later.
        """

        request = reqs.Request(
            "GET",
            f"{self.tracker_url}/ajax.php",
            headers={"Authorization": self.tracker_api_key},
            params=params,
        ).prepare()

        try:
            resp = self.session.send(request, only_if_cached=True)
            resp.raise_for_status()
            body = resp.json()

            if body["status"] == "success":
                return body
        except reqs.exceptions.HTTPError as e:
            if not (e.response.status_code == 504):
                raise e

        request = reqs.Request(
            "GET",
            f"{self.tracker_url}/ajax.php",
            headers={"Authorization": self.tracker_api_key},
            params=params,
        ).prepare()

        resp = self.send_ratelimited_request(request)
        resp.raise_for_status()
        body = resp.json()

        if body["status"] != "success":
            logger.warning("tracker error: %s", body)
            raise Tracker.StatusError(body)

        return body

    # Dumbass-grade rate limiter. Pinky swear to use this for every request to the
    # tracker RED says 10 requests per 10 seconds, but keep it lower for the time
    # being, to have more time to catch errors in program output
    @sleep_and_retry("tracker", log_level=logging.INFO)
    @limits(calls=10, period=15)
    @sleep_and_retry("tracker", log_level=logging.INFO)
    @limits(calls=8, period=10)
    @sleep_and_retry("tracker", log_level=logging.INFO)
    @limits(calls=1, period=1)
    def send_ratelimited_request(self, request: reqs.PreparedRequest):
        with self.session.cache_disabled():
            return self.session.send(request)

    def format_group_link(self, group_id) -> str:
        return f"{self.tracker_url}/torrents.php?id={group_id}"

    def format_torrent_link(self, torrent_id: int, group_id=None) -> str:
        # group_id does not seem to be really needed, torrent_id is sufficient
        # to open the group page. just a nice-to-have
        if group_id is not None:
            return f"{self.tracker_url}/torrents.php?id={group_id}&torrentid={torrent_id}#torrent{torrent_id}"
        else:
            return f"{self.tracker_url}/torrents.php?torrentid={torrent_id}#torrent{torrent_id}"
