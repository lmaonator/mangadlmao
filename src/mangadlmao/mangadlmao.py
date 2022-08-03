import argparse
from pathlib import Path
from typing import Any

import appdirs
import yaml

from mangadlmao.apis.mangadex import MangaDex
from mangadlmao.apis.mangasee import MangaSee

APPNAME = "mangadlmao"
CONFIG_DIR = Path(appdirs.user_config_dir(APPNAME))
CONFIG_FILE = CONFIG_DIR / 'config.yml'
DEFAULT_CONFIG = {
    'download_directory': '.',
    'lang': ['en'],
    'manga': [],
}


def load_config() -> dict[str, Any]:
    try:
        with CONFIG_FILE.open() as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        config = DEFAULT_CONFIG
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store_true',
                        help="print location of configuration file")
    args = parser.parse_args()
    if args.config:
        print(f"Configuration file: {CONFIG_FILE}")
        return

    config = load_config()
    download_dir = Path(config.get('download_directory'))
    if not download_dir.exists():
        print(f'config error: download_directory does not exist: {download_dir}')
        return
    default_languages = config.get('lang', DEFAULT_CONFIG['lang'])
    if not config.get('manga'):
        print('No manga in configuration file')
        return

    md = MangaDex()
    ms = MangaSee()
    manga: dict[str, Any]
    for manga in config.get('manga'):
        if 'id' in manga:
            # MangaDex
            lang = default_languages if not manga.get('lang') else manga.get('lang')
            print(f"Downloading MangaDex manga {manga.get('title')} ({manga['id']}) in languages {', '.join(lang)}"
                  f" to {download_dir}")
            md.download_manga(manga['id'], manga.get('title'), lang, download_dir, since=manga.get('since'))
        elif 'rss' in manga:
            # MangaSee
            print(f"Downloading MangaSee manga {manga.get('title')} ({manga['rss']}) to {download_dir}")
            ms.download_manga(manga['rss'], manga.get('title'), download_dir, since=manga.get('since'))
