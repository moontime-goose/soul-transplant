from typing import List

from pydantic import BaseModel


class CatalogDownloadId(BaseModel):
    catalog_id: str
    download_id: int
    type: str


class FileDownload(BaseModel):
    download_name: str
    reference_name: str
    reference_size: int


class Shard(BaseModel):
    catalog_ids: List[CatalogDownloadId]
    files: List[FileDownload]
    reference_folder: str
