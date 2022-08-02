# mangadlmao

A cli script to download and package manga into cbz for Komga.

## Configuration File

The configuration file is YAML:

```yaml
---
download_directory: "."

# default languages to download, can be overridden per manga
lang:
  - en

manga:
  - title: Tokyo Meow Meow
    id: aed24b2e-b574-4204-9702-cda5cfc567de
    # skip chapters uploaded before specified date:
    since: 2020-12-24

  - title: Yofukashi no Uta
    id: 259dfd8a-f06a-4825-8fa6-a2dcd7274230
    # override default languages:
    lang: [en, de]
```
