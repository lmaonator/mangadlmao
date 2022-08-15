# mangadlmao

A cli script to download and package manga into CBZ for [Komga].

What sets it apart from other scripts like this is that it creates
a `ComicInfo.xml` for [Komga] and it will also download any updated chapters
to get the latest versions with fixes.

## Installation

Install using [pip]:

```console
$ python -m pip install mangadlmao
```

## Usage

This script is primarily meant to be used with a [configuration file](#configuration).

However, there are some command line arguments that can be used:

```console
$ mangadlmao --help
Usage: mangadlmao [OPTIONS] [URL]...

  Download Manga from the configuration file or URL arguments.

Options:
  -c, --config PATH   Print or set configuration file path.
  -j, --jobs INTEGER  Number of parallel chapter page downloads.  [default: 4]
  -l, --lang TEXT     Language to download when URLs are given, can be
                      provided multiple times.
  -e, --exclude TEXT  Scanlation groups and users to exclude, can be provided
                      multiple times.
  --help              Show this message and exit.
```

The default download directory is the current directory. You can change it through the
[configuration file](#configuration).

## Configuration

You can get the location of the configuration file by running `mangadlmao -c`
or specify a custom path with `mangadlmao --config=/custom/location/config.yml`

The configuration file format is YAML:

```yaml
---
download_directory: "."

# default languages to download, can be overridden per manga
lang:
  - en

# global exclude for groups and users, can be name or id
exclude:
  - TerribleMachineTranslator
  - 33171bc6-0c2a-40d7-9cca-120ac52f09ae

manga:
  # entries with url can be either MangaDex or MangaSee
  - url: https://mangadex.org/title/15931821-1a3a-4aee-b27c-1c95d8d5dcf1/hololive-yohane-s-twitter-shorts
    title: Hololive Shorts by Yohane
    # skip chapters uploaded before specified date:
    since: 2020-12-24
    # override default languages (only works for MangaDex):
    lang: [en, de]
    # additional group and user excludes (only works for MangaDex):
    exclude:
      - AnotherTerribleMachineTranslator

  # entries with id are treated as MangaDex entries
  - title: Nice Manga Title
    id: aed22b2e-b544-4204-9702-cdf5cfc167de

  # entries with rss are currently treated as MangaSee entries
  - title: Manga 69
    rss: https://mangasee/rss/Manga-69.xml
    since: 2020-12-24
    # lang has no effect with MangaSee
```

The `title` key is optional but because it is used as directory name, if the name changes
server-side, all chapters will be re-downloaded into a new directory.

# Development

Install the package as editable and install development dependencies from `requirements.txt`:

```console
$ python -m pip install -e .[dev]
$ python -m pip install -r requirements.txt
```

Then install [pre-commit]:

```console
$ pre-commit install
```

Configure git to use the `.git-blame-ignore-revs` file:

```console
$ git config blame.ignoreRevsFile .git-blame-ignore-revs
```

[komga]: https://komga.org/
[pip]: https://pip.pypa.io/en/stable/
[pre-commit]: https://pre-commit.com/
