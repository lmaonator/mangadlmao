from pathlib import Path
from typing import Any

import appdirs
import click
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


@click.command()
@click.option('-c', '--config', is_flag=False, flag_value='', default=CONFIG_FILE, type=click.Path(),
              help='print location of configuration file or use custom location')
def main(config):
    if config == '':
        click.echo(f"Configuration file: {CONFIG_FILE}")
        return
    else:
        try:
            with open(config) as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            if config != CONFIG_FILE:
                raise
            config = DEFAULT_CONFIG

    download_dir = Path(config.get('download_directory'))
    if not download_dir.exists():
        click.echo(f'config error: download_directory does not exist: {download_dir}')
        return
    default_languages = config.get('lang', DEFAULT_CONFIG['lang'])
    if not config.get('manga'):
        click.echo('No manga in configuration file')
        return

    md = MangaDex()
    ms = MangaSee()
    manga: dict[str, Any]
    for manga in config['manga']:
        if 'id' in manga:
            # MangaDex
            lang = default_languages if not manga.get('lang') else manga.get('lang')
            click.echo(f"Downloading MangaDex manga {manga.get('title')} ({manga['id']}) in languages "
                       f"{', '.join(lang)} to {download_dir}")
            with click.progressbar(length=1000, item_show_func=lambda n: f'Chapter {n}' if n else None) as bar:
                def callback(progress: int = None, length: int = None, chapter: str = None):
                    if length is not None:
                        bar.length = length
                    if progress is not None:
                        bar.update(progress, chapter)
                md.download_manga(manga['id'], manga.get('title'), lang, download_dir, since=manga.get('since'),
                                  progress_callback=callback)
        elif 'rss' in manga:
            # MangaSee
            click.echo(f"Downloading MangaSee manga {manga.get('title')} ({manga['rss']}) to {download_dir}")
            with click.progressbar(length=1000, item_show_func=lambda n: f'Chapter {n}' if n else None) as bar:
                def callback(progress: int = None, length: int = None, chapter: str = None):
                    if length is not None:
                        bar.length = length
                    if progress is not None:
                        bar.update(progress, chapter)
                ms.download_manga(manga['rss'], manga.get('title'), download_dir, since=manga.get('since'),
                                  progress_callback=callback)
