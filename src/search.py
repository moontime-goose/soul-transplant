from src.model import Album
from src.utils import flatten


def normalize_query(s: str) -> str:
    allowed_special_characters = ['"']
    return " ".join(
        "".join(
            c.lower() if c.isalnum() or c in allowed_special_characters else " " for c in s
        ).split()
    )


def make_search_strings(album: Album) -> list[str]:
    return [
        normalize_query(s)
        for s in [
            rf""""{album.artist}" "{album.name}" {album.year or ""}""",
            rf"""{album.artist} {album.name}""",
        ]
    ]
