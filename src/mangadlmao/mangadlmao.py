import argparse
from pathlib import Path
from typing import Any

import appdirs
import yaml

from mangadlmao.apis.mangadex import MangaDex

APPNAME = "mangadlmao"
CONFIG_DIR = Path(appdirs.user_config_dir(APPNAME))
CONFIG_FILE = CONFIG_DIR / 'config.yml'
DEFAULT_CONFIG = {
    'download_directory': '.',
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
    if not config.get('manga'):
        print('No manga in configuration file')
        return

    md = MangaDex()
    for manga in config.get('manga'):
        print(f"Downloading manga {manga['title']} ({manga['id']}) to {download_dir}")
        md.download_manga(manga['id'], manga['title'], download_dir)
