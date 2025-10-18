import collections
import logging
import os
import os.path as path
from collections import Counter, defaultdict

import jellyfish
from rich import print

import src.app as app
from src.model import Album, Filelist, FilelistEntry
from src.search import normalize_query
from src.utils import *

logger = app.get_logger()


class FileEntryMatch:
    reference: FilelistEntry
    suggested: FilelistEntry
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

    if not music_files:
        return None

    # Too few files to have a full match, bail.
    #
    # TODO: This could be changed to compare against the count of music files,
    # assuming some users will not keep (or announce) extra files in their
    # shares.
    if len(suggestion_list.files) < len(music_files):
        return None
    # Prime candidate for improvement - O(m*n) complexity here
    file_matches_dict = defaultdict(list)
    for ref_entry in sorted(music_files, key=lambda entry: entry.name):
        for s_entry in suggestion_list.files:
            match_percentage = file_entry_similarity(album, ref_entry, s_entry)
            if match_percentage >= 50:
                file_matches_dict[ref_entry.name].append(
                    FileEntryMatch(ref_entry, s_entry, match_percentage)
                )

    # There's a guy/gal on soulseek who has a huge boxset, with every file but
    # one moved to a renamed directory, and this fix is dedicated to them.
    #
    # Pick the most common directory and try to pick all the files from it. If
    # same directory condition is not satisfied, then flat hierarchy check below
    # will filter it out, but such is life. Keep in now, hoping that flat folder
    # hierarchy check can be removed someday

    if len(file_matches_dict) == 0:
        return None

    dirnames = Counter(
        (
            os.path.dirname(m.suggested.name)
            for matches in file_matches_dict.values()
            for m in matches
        )
    )

    logger.debug("Base folder counter: %s", dirnames)

    preferred_dirname = (dirnames.most_common() or [("", 0)])[0][0]

    logger.debug("Base folder counter: %s. Chosen directory: %s", dirnames, preferred_dirname)

    file_matches: list[FileEntryMatch] = [
        min(
            matches,
            key=lambda m: m.similarity * m.suggested.name.startswith(preferred_dirname),
        )
        for (_, matches) in file_matches_dict.items()
    ]

    if len({m.reference.name for m in file_matches}) != len(
        {m.suggested.name for m in file_matches}
    ):
        logger.debug("file matching glitch: one matched to many, skip")
        return None

    # Too few files matched. This script does not intend to assemble a single
    # torrent contents from multiple users, bail
    if len(file_matches) < len(music_files):
        if len(file_matches) > len(music_files) / 2:
            # Notable enough to log, might be room for improvement if the file matching
            logger.debug(
                "Incomplete match: %d out of %d tracks, skip", len(file_matches), len(music_files)
            )
        return None

    # Another ghetto fix: sort and pick first len(music_files) items, hoping
    # they're in the same directory. If not, be conservative and drop the match
    file_matches.sort(key=lambda m: m.suggested.name)

    if len(file_matches) > len(music_files):
        logger.debug(
            "Truncating match list from %d to %d files", len(file_matches), len(music_files)
        )
        file_matches = file_matches[: len(music_files)]

    # Another check for proper nesting, or rather absence of it. Ensure that
    # soulseek album folder and torrent base folder are named the same
    #
    # TODO this is slskd-specific, move out when ready
    commonpath = os.path.commonpath(entry.suggested.name for entry in file_matches)

    # Filter out multi-cd albums with per-cd folders - nested folder download is
    # not supported by slskd, and this script does not handle this
    if any(
        entry.suggested.name != os.path.join(commonpath, os.path.basename(entry.suggested.name))
        for entry in file_matches
    ):
        logger.debug("Music folder hierarchy is not flat and not supported, skip")
        return None

    # By now - there is a match. Assemble list of files to enqueue for download.
    file_map = {entry.name: entry for entry in suggestion_list.files}

    download_music_info: list[FilelistEntry] = [
        file_map[entry.suggested.name] for entry in file_matches
    ]

    folder_extra_file_names: list[FilelistEntry] = [
        entry
        for entry in suggestion_list.files
        if os.path.dirname(entry.name) == commonpath and entry not in download_music_info
    ]

    for entry in folder_extra_file_names:
        logger.info(
            "Add extra files from the music folder: %s (%d bytes)",
            os.path.basename(entry.name),
            entry.size,
        )

    download_list = Filelist(
        folder_name=os.path.basename(commonpath),
        files=download_music_info + folder_extra_file_names,
        meta=suggestion_list.meta,
    )
    list_match = FilelistMatch(
        suggested_folder=os.path.basename(commonpath),
        files=file_matches,
        download_list=download_list,
        reference_list=reference_list,
    )

    return list_match


def file_entry_similarity(album: Album, reference: FilelistEntry, candidate: FilelistEntry) -> int:
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

    if reference.size != candidate.size:
        return 0

    name1, name2 = path.basename(candidate.name), path.basename(reference.name)
    if path.basename(name1) == path.basename(name2):
        return 100

    ext1 = path.splitext(name1)[1]
    ext2 = path.splitext(name2)[1]
    if ext1 != ext2 and (ext1 and ext2 and ext1.lower() != ext2.lower()):
        return 0

    name1, name2 = path.splitext(name1)[0], path.splitext(name2)[0]

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
    original_similarity = jellyfish.jaro_winkler_similarity(name1, name2)
    normalized_similarity = jellyfish.jaro_winkler_similarity(
        normalize_query(name1), normalize_query(name2)
    )

    # This is meant to offset similarity-happy metrics above. Jaccard metric
    # will match words as n-grams, so it won't accept typos
    track_name_similarity = jellyfish.jaccard_similarity(
        strip_to_track_name(name1), strip_to_track_name(name2)
    )

    # Orderer weighted average - lean towards the medium result
    [x1, x2, x3] = sorted([normalized_similarity, track_name_similarity, original_similarity])
    aggregate_similarity = (x1 * 2 + x2 * 4 + x3 * 2) / 8

    to_percentage = lambda x: int(round(x * 100))
    logger.debug(
        "Inexact match '%s' -> '%s': agg %d%% orig %.2f norm %.2f track %.2f",
        name1,
        name2,
        to_percentage(aggregate_similarity),
        original_similarity,
        normalized_similarity,
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
    dir_matches = reference_folder == suggested_folder
    files_match = all(m.similarity == 100 for m in list_match.files)

    max_filename_len = max(len(m.reference.name) for m in list_match.files)
    column_length = max([max_filename_len, len(reference_folder)])

    dir_header_line = color_line(
        f"{'Reference folder':{column_length+8}}\t||\t{'Matched folder'}",
        dir_matches,
    )
    match_message.append(dir_header_line)

    dir_match_line = color_line(
        f"{reference_folder:{column_length+8}}\t<-\t{suggested_folder}", 100 * dir_matches
    )
    match_message.append(dir_match_line)
    match_message.append("")

    files_header_line = color_line(
        f"{'Reference files':{column_length}}\t\t<-\t{'Matched files'}",
        dir_matches,
    )
    match_message.append(files_header_line)

    for m in list_match.files:
        line = color_line(
            f"  {m.similarity:3}%\t{m.reference.name:{column_length}}\t<-\t{os.path.basename(m.suggested.name)}",
            m.similarity == 100,
        )
        match_message.append(line)

    if not dir_matches:
        match_message.append("[red]Directory name mismatch[/]")

    if not files_match:
        match_message.append("[red]File name(s) mismatch[/]")

    return "\n".join(match_message)


def prompt_match_confirmation(config: Config, list_match: FilelistMatch, prompt) -> bool:
    match_message = format_match(list_match)

    reference_folder = list_match.reference_list.folder_name
    suggested_folder = list_match.suggested_folder
    dir_matches = reference_folder == suggested_folder
    files_match = all(m.similarity == 100 for m in list_match.files)

    return prompt_yes_no(
        config,
        f"\n{match_message}\n{color_line(prompt, dir_matches and files_match)}",
        default=True,
        log_auto=logging.INFO,
        force_user=True,
    )
