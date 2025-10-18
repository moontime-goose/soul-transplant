import glob
import json

import pytest

from src.file_match import attempt_filelist_match, format_match
from src.logger import get_logger
from src.model import Album, Filelist

logger = get_logger()


class TestFilelistMatch:

    INPUT_LIST = glob.glob("test/inputs/filelist/*.json")

    @pytest.mark.parametrize("input_path", INPUT_LIST)
    def test_replay_filelist(self, input_path):
        logger.info("Testing with %s", input_path)
        playlist = json.load(open(input_path, "rb"))

        album = Album.model_validate(playlist["album"])
        expected_failed = playlist["failed"]
        filelists = [
            (
                Filelist.model_validate(fl["reference_list"]),
                Filelist.model_validate(fl["suggested_list"]),
            )
            for fl in playlist["inputs"]
        ]

        matches = enumerate(
            attempt_filelist_match(album, suggested, reference)
            for (reference, suggested) in filelists
        )

        for i, m in matches:
            if i in expected_failed:
                assert m is None, f"Sample {i} is expected to fail, got {format_match(m)}"
            else:
                assert m, f"Sample {i} is expected to succeed, got None"
