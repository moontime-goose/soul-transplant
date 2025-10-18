import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests as reqs
from ratelimit import limits, sleep_and_retry

import src.app as app
from src.model import Album, GroupDetails, SearchResult, TorrentDetails
from src.utils import *
from src.utils import cache_path

logger = app.get_logger()


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

    CACHE_TABLE_NAME = "tracker"

    def __init__(self, tracker_url: str, tracker_api_key: str):
        self.tracker_url = tracker_url
        self.tracker_api_key = tracker_api_key

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

            body = self.make_request(params)

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

        body = self.make_request({"action": "torrentgroup", "id": group_id})
        details = GroupDetails.model_validate(body["response"])

        return details

    def get_torrent_details(self, torrent_id: int) -> TorrentDetails:
        """
        Request torrent details for the given id (notably, file listing)
        """

        body = self.make_request({"action": "torrent", "id": torrent_id})
        details = TorrentDetails.model_validate(body["response"])

        return details

    def download_torrent(self, torrent_id: int, dest_file_path: str):
        """
        Download .torrent file and save it to given file
        """

        resp = self.send_request({"action": "download", "id": torrent_id})

        resp.raise_for_status()

        with open(dest_file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def make_request(self, params) -> dict:
        """
        Primary way to make request for mostly static data on the tracker.
        Caches successful responses to avoid repeating request to tracker later.
        """

        resp = self.send_request(params)
        resp.raise_for_status()

        body = resp.json()

        if body["status"] != "success":
            raise Tracker.StatusError(body)

        return body

    # Dumbass-grade rate limiter. Pinky swear to use this for every request to the
    # tracker RED says 10 requests per 10 seconds, but keep it lower for the time
    # being, to have more time to catch errors in program output
    @sleep_and_retry("tracker", log_level=logging.INFO)
    @limits(calls=3, period=4)
    def send_request(self, params):
        return reqs.request(
            "GET",
            f"{self.tracker_url}/ajax.php",
            headers={"Authorization": self.tracker_api_key},
            params=params,
        )

    def format_group_link(self, group_id) -> str:
        return f"{self.tracker_url}/torrents.php?id={group_id}"

    def format_torrent_link(self, torrent_id: int, group_id=None) -> str:
        # group_id does not seem to be really needed, torrent_id is sufficient
        # to open the group page. just a nice-to-have
        if group_id is not None:
            return f"{self.tracker_url}/torrents.php?id={group_id}&torrentid={torrent_id}#torrent{torrent_id}"
        else:
            return f"{self.tracker_url}/torrents.php?torrentid={torrent_id}#torrent{torrent_id}"
