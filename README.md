# soul-transplant toolbox
> Search [Soulseek](https://www.slsknet.org/news/node/680) for albums that can be
cross-seeded to Gazelle music trackers.

NOTE: this is at proof-of-concept stage, volunteers for testing are welcome. 

This set of scripts will use Gazelle API (no webpage scraping) to find good-looking album folder and files, and will search Soulseek for exactly, or at least approximately) the same files. 
These files are then checked and cross-seeded using .torrent files obtained from tracker.

## Thanks

- [slskd](https://github.com/slskd/slskd/tree/master) and [slskd-python-api](https://github.com/bigoulours/slskd-python-api) dev teams, availability of the API allowed be to get these scripts up to speed quite quickly
- fine folks on RED, and tooling authors ([gazelle-origin](https://github.com/x1ppy/beets-originquery), [gazelle-origin](https://github.com/x1ppy/gazelle-origin), among others)
- check dependencies section in pyproject.toml for full list of credits

## Features

- normalize queries to possibly find more matches.

- Identify matches using known file names and file sizes.

- Allow approximate user-confirmable  matches on file names.

- Cache tracker and slskd API responses and torrent files locally.

- Transpant downloaded files into qBittorrent

## Limitations

- No matching for multi-cd nested music folders. 
  slskd does not handle nested downloads, neither in the API nor web UI.
  
- No matching on non-music files. 
  Files are matched by names and sizes, names don't vary that much, and file sizes are small enough for collisions

- Early stages of development. 
  Don't be shocked by occasional uncaught exception.

<details>
<summary>Example of a usage</summary>

``` sh
>>> uv run soul-snatch.py -f albumlist.json
INFO     Config file found at ~/..config/soul-transplant/config.yaml


>>> Search for album: name='Whenever You Need Somebody'? [y/n] (y): y
Search for album: name='Whenever You Need Somebody'
INFO     Searching tracker for Rick Astley - Whenever You Need Somebody
INFO     Got page 1 out of 1 (2 group, 14 torrents) for Whenever You Need Somebody
INFO     Accept group $TRACKER_GROUP_LINK
INFO     Accept group   $TRACKER_TORRENT_LINK
INFO     search Soulseek for 12 candidates
INFO     Found match $TRACKER_TORRENT_LINK from $SOULSEEK_USER
>>>
$ARTIST - $ORIGINAL_ALBUM_FOLDER    ->      $ARTIST - $SOULSEEK_ALBUM_FOLDER
  100%  01 Never Gonna Give You Up.flac                 ->      01 Never Gonna Give You Up.flac
   23%  02 Never Gonna Let You Down.flac                ->      02 Whenever You Need Somebody.flac
  100%  03 $ORIGINAL_TRACK_NAME                         ->      03 $SOULSEEK_TRACK_NAME
  100%  04 $ORIGINAL_TRACK_NAME                         ->      04 $SOULSEEK_TRACK_NAME
  100%  05 $ORIGINAL_TRACK_NAME                         ->      05 $SOULSEEK_TRACK_NAME
  100%  06 $ORIGINAL_TRACK_NAME                         ->      06 $SOULSEEK_TRACK_NAME
  100%  07 $ORIGINAL_TRACK_NAME                         ->      07 $SOULSEEK_TRACK_NAME
  100%  08 $ORIGINAL_TRACK_NAME                         ->      08 $SOULSEEK_TRACK_NAME
  100%  09 $ORIGINAL_TRACK_NAME                         ->      09 $SOULSEEK_TRACK_NAME
  100%  10 $ORIGINAL_TRACK_NAME                         ->      10 $SOULSEEK_TRACK_NAME
Accept match from user $SOULSEEK_USER? [y/n] (y): y
INFO     $SOULSEEK_USER, thanks! $ARTIST - $ORIGINAL_ALBUM_FOLDER found
INFO     Matched 1 torrents, try 11 folder name searches
INFO     Matched 1 more torrents to Soulseek

>>> uv run soul-transplant.py $SOULSEEK_FOLDER/$ALBUM_FOLDER
INFO     Config file found at ~/.config/soul-transplant/config.yaml
INFO     Got 1 shards
Old: 02 Never Gonna Let You Down.flac
New: 02 Whenever You Need Somebody.flac
Rename? [y/n] (y): y
INFO     Got 1 possible torrents to add
INFO     Got 1 new torrents to add
INFO     qbittorrent torrent add return Ok.
INFO     Wait for torrents to be added
INFO     Wait for torrents to be added
INFO     Wait for torrents to be added
Torrent recheck triggered, check qBittorrent interface
```

</details>

Edited down for demonstrational and slightly comedic purposes.
Actual log is colorized to identify attention points, like filename mismatches.

## Prerequisites

- [ ] python >= 3.10 (older version are probably fine, but not tested)

- [ ] account on the tracker with a configured API key (check the wiki)

- [ ] account on Soulseek

- [ ] [slskd](https://github.com/slskd/slskd/tree/master) instance with a configured API key (check the [documentation](https://github.com/slskd/slskd/blob/master/docs/config.md#authentication))

## Considerations

- These scripts use slskd, because I use slskd.
  The web UI part has its issues, but the download/upload/api parts work just fine.
  More importantly, it seems to be the only proper client out there that is easy to set up in a container, set it up with VPN, and keep running 24/7 to properly participate in the Soulseek network.
- Best care is taken to ensure no file loss, but be careful nonetheless.
- Approximately 80% of the code was written under the influence of caffeine (as in the tea competitor, not what you might have misread). 
  Make of that what you will.
- While there is potential for automation, and improving autoconfirmations, that's on hold, until and unless some tests are implemented.

### API limits

Both Soulseek and tracker API have rate limits.
These scripts account for those limits, but it is also up to the user to be mindful of them, even if it will take a while for a search to complete.

## Installation

Clone and use `uv run` to run the scripts, like this:

``` sh
git clone https://github.com/moontime-goose/soul-transplant.git
cd soul-transplant
uv run soul-transplant.py [PARAMETERS...]
```

Assume that development is still active, updates may be frequent, up to an including renaming and moving any python file. 
`pip install` should be doable, but not recommended at this point

## Usage

This project will be developed as a set of scripts. For the moment, these are:

- soul-snatch.py - look for files on Soulseek that match an upload on the tracker, and enqueue the Soulseek download

- soul-transplant.py - import completed album downloads to qbitorrent, to check against the corresponding torrent files, and to cross-seed

Suggested workflow:

### - Manage your expectations

- Don't expect to find everything with this workflow. 
  Based on my observations, chances to find a particular album increase with the number of seeders, snatchers, and the number of versions (Vinyl/CD/WEB, boxset/compilations, etc). 
  You may increase your seed size with the tracker, but the stuff will already be likely superbly, so not a lot of upload credit to gain in the near future.
  
- Relevant APIs are rate limited. A single album search may take minutes (plural).

### - Check the CLI help message(s)

Main options are:

```
  -h, --help            show this help message and exit
  -f INPUT_FILE, --file INPUT_FILE
                        File with the list of albums to look up
  --log [{debug,info,warning,error,critical}]
                        Logging level for the application
  -c [CONFIG_PATH], --config [CONFIG_PATH]
                        Path to configuration file. Default: config will we search for in script and XDG config directories
  --timid               Ask for user confirmation on every interaction. Default: queries autoconfirm, downloads/deletions and potentially destructive actions will require confirmation

```

Recommended extra options for the first run are `--timid --log debug`. 
Logs should be clear enough to understand what the script is doing with your API keys and files.

### - Ensure that configuration file exists

Scripts will look for `config.yaml` file in:

- the script folder

- your XDG config folder, e.g. "~/.config/soul-transplant" on linux

Alternatively, use `-c`, or `--config` to specify path to config file

Refer to `config.default.yaml` for an example of the configuration

*DO NOT put your main music library path in the config*. 

Currently, scripts have some default hard-coded in the code. 
Main ones are about media format - scripts will look only for ".flac" files on souldseek and FLAC/Lossless/24bit Lossless uploads on tracker.
These defaults may or may not be moved to config file and CLI options later.


### - Prepare input list

`soul-snatch.py` expects a file which contains a json array, with entries of the
following format:

```json
[
    {
        "album": "Whenever You Need Somebody",
        "albumartist": "Rick Astley",
        "original_year": "1987"
    }
]
```

Coincidentally, `beet export` plugin can be used to generate output in this format, given that you have this album in your library:

```sh
beet export -a -i '' Whenever You Need Somebody > albumlist.json
```

### - Snatch albums from Soulseek

``` sh
uv run soul-snatch.py -f ~/tmp/albumlist.json

# or
uv run soul-snatch.py -f ~/tmp/albumlist.json --timid # Ask user for confirmation on most steps

# or
uv run soul-snatch.py -f ~/tmp/albumlist.json --log debug # Output a lot of extra information
```

Script will query tracker and Soulseek, and enqueue Soulseek downloads which may be cross-seedable.
These downloads, once completed, will be usable by `soul-transplant.py`.

Use your slskd web UI to track progress of the downloads. 
Due to the nature of Soulseek (and, probably, slskd) you may get an error, even if all the files were
enqueued.
slskd appears to do retries (?), completing the download eventually, but scripts will not track this.
Even it slskd returned an error, as long as the
files are downloaded, they can be processed, or you can just listen to the music.

`soul-snatch.py` leaves behind `soul-shard.yaml` metadata files, for further use by `soul-transplant.py`, and potentially other scripts.
One of each should be created in album download folder, looking approximately like this.

``` yaml
files:
- download_name: A1 - Intro.flac
  original_name: A1 - Intro.flac
  original_size: 1234567
- download_name: A2 - Apocalypse Please.flac
  original_name: A2 - Apocalypse Please.flac
  original_size: 8901234
  # MORE files here
root: ${YOUR_SLSKD_DOWNLOAD_FOLDER}/${ALBUM_FOLDER_NAME}
torrent_id: 987654321
```

Currently, once created, these files will not be removed. 
It's up to user to check and remove empty folder. 
If scripts sees the album folder with the same name as one of a potential download, download will be skipped.


### - Wait for download completion

Currently, there's no Soulseek download tracking - too fickle, and too boring to cover the edge cases.

### - Import to qbittorrent

```sh
uv run soul-transplant.py --log debug DIRECTORY_WITH_SHARD [DIRECTORY_WITH_SHARD...]
```

`soul-transplant.py` checks that shard file description matches directory contents, renaming files according to the information in the `soul-shard.yaml`.

User will be prompted on every rename operation, or a missing file in a folder.
So, you can actually try and import a folder even if some files failed to download.

Once checked and confirmed, corresponding torrent files will be downloaded and added to qBittorrent and forces recheck to see how much of the album folder is re-usable.
Torrents are added:

 - in a paused state
 
 - with no category or tags assigned
 
 - with disabled automatic torrent management disabled

*qBittorrent may change or rename files (adding '.!qB') extension based on your settings*.
However, the files should still stay in the original folder.

### - Check qBittorrent status

Once scripts completed, qBittorrent will have some torrents added, in a paused
state, and ideally with 90+% matching file contents.
From here, they can be resumed to complete the download (artwork, cue/log files, maybe a missing track).
Please keep sharing from here on out.

### - [Optional] Use gazelle-origin to fetch tracker metadata

See [gazelle-origin](https://github.com/spinfast319/gazelle-origin) documentation for ways to integrate it in qbittorrent to get `origin.yaml` for free.

Otherwise, downloaded `.torrent` files are stored in ``~/.config/soul-transplant/cache` folder, and they can be passed to gazelle-origin directly, or by id from the filenames.


## Project structure

- `soul-snatch.py` - find matches on Soulseek
- `soul-transplant.py` - import cross-seed downloads to qBittorrent
- `slskd-clear-searches.py` - clear search history from slskd
- `validate-config.py` - ensure that configuration files that will be used by other scripts are valid

`soul-snatch.py` and `soul-transplant.py` are the mainentry points.

`src` folder contain implementation shared between the scripts: tracker and slskd api, logger, config parser, etc

For pre-commit formatting and checks:

``` sh
uvx isort . && uvx black . && uvx ty check && uvx pyright
```

