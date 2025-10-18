import pytest

from src.file_match import file_entry_similarity
from src.model import Album, FilelistEntry


class TestFileMatch:
    def test_sanity(self):
        assert 2 + 2 == 4

    ALBUM_PARAMS = {"artist": "Organica", "name": "Master of Membranes", "year": 1896}
    TRACK_PARAMS = {"name": "01. Mitochondria.flac", "size": 12345678}
    ALBUM = Album.model_validate(ALBUM_PARAMS)
    TRACK = FilelistEntry.model_validate(TRACK_PARAMS)

    # Exact match
    TESTDATA_BASIC_FILE_MATCH = [
        [ALBUM, TRACK, TRACK],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_BASIC_FILE_MATCH)
    def test_basic_file_match(self, album, reference, candidate):
        assert file_entry_similarity(album, reference, candidate) == 100

    # Case mismatch is a minor issue, score should be high
    TESTDATA_FILE_CASE_MATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. mitochondria.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. MitochondriA.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. MITOCHONDRIA.flac"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_CASE_MATCH)
    def test_file_case_match(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity >= 85
        assert similarity < 100

    # One-edit distance typo is a bit worse, but still ok
    TESTDATA_FILE_TYPO_MATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochodria.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mtochondria.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondriaa.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "1. Mitochondria.flac"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_TYPO_MATCH)
    def test_file_typo_match(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity >= 80

    # Multiple typos are worse, but still a possible match as long as the strings are close enough
    TESTDATA_FILE_TYPOS_MATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochonri.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochonri.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. mitochonri.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. mitochodry.flac"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_TYPOS_MATCH)
    def test_file_typos_match(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity >= 70
        assert similarity <= 85

    # Popular naming schemes from beets and lidarr library managers
    TESTDATA_FILE_NAMING_SCHEME_MATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate(
                {**TRACK_PARAMS, "name": "Organica - Master of Membranes - 01 - Mitochondria.flac"}
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate(
                {**TRACK_PARAMS, "name": "Master of Membranes - 01 - Mitochondria.flac"}
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "A1 - Mitochondria.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate(
                {
                    **TRACK_PARAMS,
                    "name": "Organica - 1896 - Master of Membranes - Mitochondria.flac",
                }
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01_mitochondria.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "organica_mitochondria.flac"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_NAMING_SCHEME_MATCH)
    def test_file_naming_scheme_match(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity >= 50

    # Whitespace may also vary, which is somewhat more acceptable than typos
    TESTDATA_FILE_WHITESPACE_MATCH = [
        [
            ALBUM,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
            FilelistEntry.model_validate(
                {**TRACK_PARAMS, "name": "05\t - Disposable      Cytoplasm.flac"}
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
            FilelistEntry.model_validate(
                {
                    **TRACK_PARAMS,
                    "name": "Organica - 1896 - Master of Membranes - 05 - Disposable T-Cells.flac",
                }
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
            FilelistEntry.model_validate(
                {
                    **TRACK_PARAMS,
                    "name": "Organica_1896_Master_of_Membranes_05_Disposable_T_Cells.flac",
                }
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
            FilelistEntry.model_validate(
                {
                    **TRACK_PARAMS,
                    "name": "Organica-1896-Master of Membranes-05-Disposable T-Cells.flac",
                }
            ),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_NAMING_SCHEME_MATCH)
    def test_file_whitespace_match(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity >= 50

    # Entirely different names should hopefully produce less-than-likely similiarity
    TESTDATA_FILE_TRACK_MISMATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "02 - Master of Membranes.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate(
                {**TRACK_PARAMS, "name": "03 - The Thing That Should Not Split.flac"}
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate(
                {**TRACK_PARAMS, "name": "04 - Welcome Home (Mitochondia).flac"}
            ),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "05 - Disposable T-Cells.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "06 - Leper Mitosis.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "07 - Organelle.flac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "08 - Damage, Org..flac"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_TRACK_MISMATCH)
    def test_file_track_mismatch(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity <= 50

    # Mismatching sizes are hard-stop
    TESTDATA_FILE_SIZE_MISMATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "size": int(TRACK_PARAMS["size"]) - 1}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "size": int(TRACK_PARAMS["size"]) + 1}),
        ],
        [ALBUM, TRACK, FilelistEntry.model_validate({**TRACK_PARAMS, "size": 0})],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "size": int(TRACK_PARAMS["size"]) * 2}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_SIZE_MISMATCH)
    def test_file_size_mismatch(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity == 0

    # Matching extensions with same basename are full match, regardless of case
    TESTDATA_FILE_EXT_MATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria.FLAC"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria.flAC"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_EXT_MATCH)
    def test_file_ext_match(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity == 100

    # Mismatching extensions are hard-stop
    TESTDATA_FILE_EXT_MISMATCH = [
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria.mp3"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria.alac"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria"}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria."}),
        ],
        [
            ALBUM,
            TRACK,
            FilelistEntry.model_validate({**TRACK_PARAMS, "name": "01. Mitochondria.exe"}),
        ],
    ]

    @pytest.mark.parametrize("album,reference,candidate", TESTDATA_FILE_EXT_MISMATCH)
    def test_file_ext_mismatch(self, album, reference, candidate):
        similarity = file_entry_similarity(album, reference, candidate)
        assert similarity == 0
