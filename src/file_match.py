import itertools
import logging
import os
import os.path as path
from collections import Counter, defaultdict
from typing import Optional

import jellyfish

import src.app as app
from src.model import Album, Filelist, FilelistEntry
from src.search import normalize_query
from src.soul_config import Config
from src.utils import prompt_yes_no, to_percentage

logger = logging.getLogger(app.LIB_LOGGER_NAME)


class FileEntryMatch:
    reference: FilelistEntry
    suggested: Optional[FilelistEntry]
    similarity: int

    def __init__(self, e1, e2, percentage):
        self.reference = e1
        self.suggested = e2
        self.similarity = percentage


class FilelistMatch:
    suggested_folder: str
    files: list[FileEntryMatch]
    download_list: Filelist
    reference_list: Filelist

    def __init__(
        self,
        suggested_folder: str,
        files: list[FileEntryMatch],
        download_list: Filelist,
        reference_list: Filelist,
    ):
        self.suggested_folder = suggested_folder
        self.files = files
        self.download_list = download_list
        self.reference_list = reference_list

    def folder_name_similarity(self):
        return int(
            100
            * jellyfish.jaro_winkler_similarity(
                normalize_query(os.path.basename(self.reference_list.folder_name)),
                normalize_query(os.path.basename(self.suggested_folder)),
            )
        )

    def overall_similarity(self) -> int:
        return int(
            round(self.folder_name_similarity() + sum(f.similarity for f in self.files))
            / len(self.files)
            + 1
        )


def attempt_filelist_match(
    album: Album, suggestion_list: Filelist, reference_list: Filelist, media_format=None
) -> FilelistMatch | None:
    """
    Attempt to match torrent files entries to entries in a response from a
    single soulseek user.
    """

    formats = [media_format] if media_format else ["FLAC", "MP3"]

    music_files = [
        ref_entry
        for ref_entry in reference_list.files
        if any(ref_entry.name.endswith(ext.lower()) for ext in formats)
    ]

    # Just not enough files from to match, bail out
    if len(suggestion_list.files) < len(reference_list.files):
        return None

    # TODO: find a place for this check outside - this does not depend on suggestion_list.
    if not music_files:
        return None

    # TODO: Prime candidate for improvement - O(m*n) complexity here

    # First - look for music file matches only, to identify album directory,
    # which hopefull has auxiliary files too.
    file_matches_dict = defaultdict(list)
    for ref_entry in sorted(music_files, key=lambda entry: entry.name):
        matched_entry = match_file_from_list(album, ref_entry, suggestion_list.files)
        if not matched_entry:
            # It any of the music files are missing, skip.
            return None

        file_matches_dict[ref_entry.name].append(matched_entry)

    # There's a guy/gal on soulseek who has a huge boxset, with every file but
    # one moved to a renamed directory, and this hack is dedicated to them.
    #
    # Pick the most common directory and try to pick all the files from it. If
    # same directory condition is not satisfied, then flat hierarchy check below
    # will filter it out, but such is life. Keep in now, hoping that flat folder
    # hierarchy check can be removed someday
    #
    # TODO: this explicitly assumes that all the music files are flat in the top
    # folder. To be rewritten when implementing suppliers other than slskd

    dirnames = Counter(
        (
            os.path.dirname(m.suggested.name)
            for matches in file_matches_dict.values()
            for m in matches
        )
    )

    preferred_dirname = (dirnames.most_common() or [("", 0)])[0][0]

    # TODO: could try every directory if there are several options with most
    # matching files, as in several copies of every file is multiple folders.
    logger.debug("Base folder counter: %s. Chosen directory: %s", dirnames, preferred_dirname)

    file_matches: list[FileEntryMatch] = [
        min(
            matches,
            key=lambda m: m.similarity * m.suggested.name.startswith(preferred_dirname),
        )
        for (_, matches) in file_matches_dict.items()
    ]

    if len({m.reference.name for m in file_matches}) != len(
        {m.suggested.name for m in file_matches if m.suggested}
    ):
        # Filename matching heuristic messed up, or maybe it's a single-track
        # black metal album with all the tracks named the same with no
        # numbering. Just skip the match for now.
        logger.debug("file matching glitch: one matched to many, skip")
        return None

    # Another check for proper nesting, or rather absence of it. Ensure that
    # soulseek album folder and torrent base folder are named the same
    #
    # TODO this is slskd-specific, move out when ready
    common_path = os.path.commonpath(
        entry.suggested.name for entry in file_matches if entry.suggested
    )

    # Filter out multi-cd albums with per-cd folders - nested folder download is
    # not supported by slskd, and this script does not handle this
    if any(
        entry.suggested.name != os.path.join(common_path, os.path.basename(entry.suggested.name))
        for entry in file_matches
        if entry.suggested
    ):
        logger.debug("Music folder hierarchy is not flat and not supported, skip")
        return None

    # By now - there is a match on music files. Check if auxiliary files can be matched too.

    extra_files = [
        ref_entry
        for ref_entry in reference_list.files
        if ref_entry not in music_files and "/" not in ref_entry.name
    ]
    common_folder_entries = [
        entry for entry in suggestion_list.files if entry.name.startswith(common_path)
    ]
    logger.debug("Looking for %d extra files", len(extra_files))

    for ref_entry in extra_files:
        matched_entry = match_file_from_list(album, ref_entry, common_folder_entries)
        if not matched_entry:
            logger.info("Cannot match non-music file %s (size %d)", ref_entry.name, ref_entry.size)
            file_matches.append(FileEntryMatch(ref_entry, None, 0))
        else:
            file_matches.append(matched_entry)

    file_map: dict[str, FilelistEntry] = {entry.name: entry for entry in suggestion_list.files}

    download_music_info: list[FilelistEntry] = [
        file_map[entry.suggested.name] for entry in file_matches if entry.suggested
    ]

    folder_extra_file_names: list[FilelistEntry] = [
        entry
        for entry in suggestion_list.files
        if os.path.dirname(entry.name) == common_path and entry not in download_music_info
    ]

    for entry in folder_extra_file_names:
        logger.info(
            "Add extra files from the music folder: %s (%d bytes)",
            os.path.basename(entry.name),
            entry.size,
        )

    download_list = Filelist(
        folder_name=os.path.basename(common_path),
        files=download_music_info + folder_extra_file_names,
        meta=suggestion_list.meta,
    )
    list_match = FilelistMatch(
        suggested_folder=os.path.basename(common_path),
        files=file_matches,
        download_list=download_list,
        reference_list=reference_list,
    )

    return list_match


def match_file_from_list(
    album: Album, ref_entry: FilelistEntry, suggested_files: list[FilelistEntry]
):
    for s_entry in suggested_files:
        match_percentage = music_file_entry_similarity(album, ref_entry, s_entry)

        # 50% si referring only to name similarity. This might be a
        # questionable heuristics and does not necessarily indicate that the
        # file contents were altered, so - might as well consider
        # remembering every >= 0 match instead
        if match_percentage >= 50:
            return FileEntryMatch(ref_entry, s_entry, match_percentage)

    return False


def music_file_entry_similarity(
    album: Album, reference: FilelistEntry, candidate: FilelistEntry
) -> int:
    """
    Check if file entries between torrent and soulseek search are a(n apparent) match
    """

    # Absolute hodge-podge of heuristic below - value mixture of metrics to
    # calculate vague similarity. This mostly works, because file size check
    # above is the main differentiator anyway.

    # Hopefully, this function will return:
    #
    # - 100 - on full name/size match (except for case-insensitive file extension check)
    #
    # - >90 - for case-only differences
    #
    # - >80 - for single-typo
    #
    # - >70 - for few typos
    #
    # - >50 - for same-titled tracks with different naming schemes (whitespace,
    #   adding/removing artist, album, year, etc)
    #
    # - <50 - for different track names

    # This is the main check in practice. FLAC files sizes are random enough to
    # be unique on, e.g. a single NAS/soulseek share, same probably goes for MP3
    # as well, even with CBR (latter is not tested). Other heuristics are trying
    # to pick less altered version of the file, based on how altered the
    # filename is, which is not ironclad, and may just be removed to simplify
    # the task on hand.
    if reference.size != candidate.size:
        return 0

    name1, name2 = path.basename(candidate.name), path.basename(reference.name)
    if path.basename(name1) == path.basename(name2):
        return 100

    name1, ext1 = path.splitext(name1)
    name2, ext2 = path.splitext(name2)

    if ext1 != ext2 and (ext1 and ext2 and ext1.lower() != ext2.lower()):
        return 0

    def strip_to_track_name(filename: str) -> str:
        return (
            normalize_query(filename)
            .replace(normalize_query(album.artist), "")
            .replace(normalize_query(album.name), "")
            .replace(f"{album.year}", "")
        )

    # Edit distance like Jaro-winkler is rather fuzzy metric. e.g. two average
    # track names will oftentimes have at least 0.5 similarity (apparently
    # because of track numbers and whitespace), especially after they're
    # normalized for whitespace and lower/uppwer case
    original_name_similarity = jellyfish.jaro_winkler_similarity(name1, name2)
    normalized_name_similarity = jellyfish.jaro_winkler_similarity(
        normalize_query(name1), normalize_query(name2)
    )

    # This is meant to offset similarity-happy metrics above. Jaccard metric
    # will match words as n-grams, so it won't accept typos. Meant to catch
    # renames where tracks were renamed to add/remove artist and album names
    track_name_similarity = jellyfish.jaccard_similarity(
        strip_to_track_name(name1), strip_to_track_name(name2)
    )

    # Orderer weighted average - lean towards the medium result
    [x1, x2, x3] = sorted(
        [normalized_name_similarity, track_name_similarity, original_name_similarity]
    )
    aggregate_similarity = (x1 * 2 + x2 * 4 + x3 * 2) / 8

    logger.debug(
        "Inexact match '%s' -> '%s': agg %d%% orig %.2f norm %.2f track %.2f",
        name1,
        name2,
        to_percentage(aggregate_similarity),
        original_name_similarity,
        normalized_name_similarity,
        track_name_similarity,
    )

    return to_percentage(aggregate_similarity)


def filename_similarity(filename1: str, filename2: str) -> float:
    """

    Calculate similarity of two filenames, using some heuristics, like
    wighing in differences in case, whitespace, punctuation, etc

    Subject to improvement. For the moment it's enough that:

    - returns 1.0 for exact match
    - returns 0.0 for sufficiently different strings
    - returns something in bettwen for close matches
    """
    normalized_name_similarity = jellyfish.jaro_similarity(
        normalize_query(filename1), normalize_query(filename2)
    )

    original_name_similarity = jellyfish.jaro_similarity(filename1, filename2)

    return min(max(0, 0.75 * normalized_name_similarity + 0.25 * original_name_similarity), 1)


def color_line(line, is_good):
    color = "green" if is_good else "yellow"
    return f"[{color}]{line}[/]"


def format_match(list_match) -> str:
    match_message = []

    reference_folder = list_match.reference_list.folder_name
    suggested_folder = list_match.suggested_folder
    folder_matches = reference_folder == suggested_folder
    folder_name_similarity = int(
        100
        * jellyfish.jaro_winkler_similarity(
            normalize_query(os.path.basename(reference_folder)),
            normalize_query(os.path.basename(suggested_folder)),
        )
    )
    files_match = all(m.similarity == 100 for m in list_match.files)

    max_filename_len = max(len(m.reference.name) for m in list_match.files)
    column_length = max([max_filename_len, len(reference_folder)])

    header_line = f"name match\t{'Reference':{column_length+8}}\tMatched"
    match_message.append(header_line)

    dir_match_line = color_line(
        f"{folder_name_similarity:3}%\t\t{reference_folder:{column_length+8}}\t<-\t{suggested_folder}",
        100 * folder_matches,
    )
    match_message.append(dir_match_line)

    for m, n in zip(list_match.files, itertools.chain(list_match.files[1:], [None])):
        tree_symbol = "├── " if n else "└── "
        line = color_line(
            f"{m.similarity:3}%\t\t{tree_symbol}{m.reference.name:{column_length}}\t<-\t{tree_symbol}{os.path.basename(m.suggested.name) if m.suggested else 'None'}",
            m.similarity == 100,
        )
        match_message.append(line)

    if not folder_matches:
        match_message.append("[red]Directory name mismatch[/]")

    if not files_match:
        match_message.append("[red]File name(s) mismatch[/]")

    avg_similarity = (
        list_match.folder_name_similarity() + sum(f.similarity for f in list_match.files)
    ) / (1 + len(list_match.files))

    match_message.append(color_line(f"Similarity: {avg_similarity:.2f}%", avg_similarity == 100))

    return "\n".join(match_message)


def prompt_match_confirmation(config: Config, list_match: FilelistMatch, prompt) -> bool:
    match_message = format_match(list_match)

    dir_similarity = list_match.folder_name_similarity()
    min_file_similarity = min(f.similarity for f in list_match.files)

    if config.confident:
        match_looks_good = dir_similarity > 50 and min_file_similarity > 50
    else:
        match_looks_good = dir_similarity == 100 and min_file_similarity == 100

    return prompt_yes_no(
        config,
        f"\n{match_message}\n{color_line(prompt, match_looks_good)}",
        default=True,
        log_auto=logging.INFO,
        force_user=not match_looks_good,
    )
