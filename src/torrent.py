import logging
from typing import Optional

import torf
from rich.progress import Progress, TaskID

from src.app import LIB_LOGGER_NAME

logger = logging.getLogger(LIB_LOGGER_NAME)


class TorrentVerifyCallback:
    pieces_valid: int = 0
    pieces_invalid: int = 0
    progress: Progress = Progress()
    task: TaskID

    def __init__(self, name, progress):
        self.progress = progress
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
        self.progress.update(self.task, total=pieces_total, advance=1)
        if exception is None:
            self.pieces_valid += 1
        else:
            self.pieces_invalid += 1


def verify_torrent(torrent_file_path: str, content_path: str) -> tuple[float, int]:
    try:
        t = torf.Torrent.read(torrent_file_path)

        with Progress() as progress:
            cb = TorrentVerifyCallback(t.name[:40] if t.name else "Progress", progress)
            t.verify(content_path, callback=cb, interval=0)

        return (cb.pieces_valid * 100.0 / (cb.pieces_valid + cb.pieces_invalid), t.size)
    except torf.TorfError as e:
        # These are downloaded, not created. If the torrent file from the
        # tracker is bad somehow, there may be bigger issues, bail altogeter
        logger.error("Unexpected error on torrent contente validation: %s", e)
        raise e
