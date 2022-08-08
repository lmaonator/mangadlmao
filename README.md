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

manga:
  # entries with url can be either MangaDex or MangaSee
  - url: https://mangadex.org/title/15931821-1a3a-4aee-b27c-1c95d8d5dcf1/hololive-yohane-s-twitter-shorts
    title: Hololive Shorts by Yohane
    # skip chapters uploaded before specified date:
    since: 2020-12-24
    # override default languages (only works for MangaDex):
    lang: [en, de]

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

[komga]: https://komga.org/
[pip]: https://pip.pypa.io/en/stable/
