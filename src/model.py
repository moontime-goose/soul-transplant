import html
import re
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel


class FilelistEntry(BaseModel):
    name: str
    size: int
    meta: dict = dict()  # python typing at its strictest. it could be typing.Any


class Filelist(BaseModel):
    folder_name: str
    files: list[FilelistEntry]
    meta: dict = dict()


def parse_filelist(v):
    # Gazelle returns file listing in a single html-escaped string of format
    # "|||".join(f"{filename}({filesize})")
    if isinstance(v, str):
        return [
            FilelistEntry(name=html.unescape(name), size=size)
            for name, size in re.findall(r"([^|{]+)\{\{\{(\d+)\}\}\}", v)
        ]
    return v


class Album(BaseModel):
    model_config = ConfigDict(validate_by_name=True)

    artist: str = Field(validation_alias="albumartist")
    name: str = Field(validation_alias="album")
    year: Optional[int] = Field(validation_alias="original_year")


class Torrent(BaseModel):
    """
    Corrensponds to responses from action=torrent, or action=torrentgroup.
    Set of fields is slightly different with action=torrent having more
    information

    """

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)

    id: int
    info_hash: Optional[str] = None  # Only gettable from action=torrent
    format: str
    encoding: str
    trumpable: bool
    file_list: Annotated[list[FilelistEntry], BeforeValidator(parse_filelist)]
    file_path: Annotated[str, BeforeValidator(html.unescape)]
    seeders: int
    snatched: int
    size: int
    file_count: int


class TorrentResult(BaseModel):
    """
    Corresponds to a single entry from action=browse, results.[torrents]
    """

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)

    encoding: str
    format: str
    torrent_id: int
    snatches: int
    seeders: int
    trumpable: bool


class SearchResult(BaseModel):
    """
    Corresponds to a single entry from action=browse, results
    """

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)

    artist: str
    group_id: int
    group_name: str
    torrents: list[TorrentResult]


class Group(BaseModel):
    """
    Corresponds to a group subobject from action=torrentgroup result
    """

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)

    id: int
    name: str
    music_info: dict


class GroupDetails(BaseModel):
    """
    action=torrentgroup return value
    """

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)

    group: Group
    torrents: list[Torrent]


class TorrentDetails(BaseModel):
    """
    action=torrent return value
    """

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)

    torrent: Torrent
    group: Group
