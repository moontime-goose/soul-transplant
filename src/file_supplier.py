import abc
from enum import Enum
from typing import Any, Iterable

from src.file_match import Filelist, FilelistMatch
from src.model import Album


class SearchException(Exception):
    """Base exception for search operations"""

    pass


class SearchFailedException(SearchException):
    """Raised when search fails"""

    pass


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
    def search_album(self, album: Album) -> Iterable[Filelist]:
        """
        Search for albums and yield filelists. May raise search exceptions.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def search_text(self, search_str: str) -> Iterable[Filelist]:
        """
        Search for files by a given string. Provider may adapt the string to be
        more efficient depending on search functionality.

        Raises:
            SearchFailedException: When search fails
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

    @abc.abstractmethod
    def is_downloadable(self, folder_match: FilelistMatch) -> bool:
        """
        Return true if the supplier can successfully download list of files
        specified by folder_match.

        At the moment, this is a carve-out for slskd which can only download
        flat folders
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def format_list_oneline(self, filelist: Filelist) -> str:
        """
        Return a print()able string which describes the filelist obtained from
        this supplier
        """
        raise NotImplementedError()
