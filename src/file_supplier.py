import abc
from enum import Enum
from typing import Any

from src.file_match import Filelist
from src.model import Filelist


class FileSupplier(abc.ABC):
    """
    Abstraction for any source from where files can actually be downloaded.
    """

    class SearchStatus(Enum):
        COMPLETE = 0
        TIMED_OUT = 1
        LIMIT_REACHED = 2
        FAILED = 3
        NOT_IMPLEMENTED = 4

    class DownloadStatus(Enum):
        COMPLETE = 0
        SCHEDULED = 1
        FAILED = 2

    @abc.abstractmethod
    def perform_search(self, search_str: str) -> tuple[SearchStatus, list[Filelist]]:
        """
        Search for files by a given string. Provider may adapt the string to be
        more efficient depending on search functionality.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def enqueue_download(self, filelist: Filelist) -> tuple[DownloadStatus, Any]:
        """
        Queue files described by the filelist for download. The download
        itself may actually be completed in here, if it's fast enough, but
        that's optional
        """
        raise NotImplementedError()
