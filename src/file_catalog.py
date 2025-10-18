import abc
from typing import Any

import src.shard as shard
from src.model import Album, Filelist


class FileCatalog(abc.ABC):
    """
    Abstraction for any source from where file listings can be obtained.
    """

    @abc.abstractmethod
    def search(self, album: Album) -> list[Filelist]:
        raise NotImplementedError

    @abc.abstractmethod
    def format_meta_link(self, filelist: Filelist) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def format_meta_download_id(self, filelist: Filelist) -> Any:
        raise NotImplementedError

    @abc.abstractmethod
    def already_exists(self, filelist: Filelist) -> bool:
        raise NotImplementedError

    def make_catalog_download_id(self, filelist: Filelist) -> shard.CatalogDownloadId:
        raise NotImplementedError
