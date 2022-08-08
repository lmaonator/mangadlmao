import re
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
              help='Print or set configuration file path.')
@click.option('-j', '--jobs', default=4, show_default=True, help='Number of parallel chapter page downloads.')
def main(config: str, jobs: int):
    if config == '':
        click.echo(f"Configuration file: {CONFIG_FILE}")
        return
    else:
        try:
            with open(config) as f:
                config: dict[str, Any] = yaml.safe_load(f)
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

    md = MangaDex(max_workers=jobs)
    ms = MangaSee(max_workers=jobs)
    manga: dict[str, Any]
    for manga in config['manga']:
        if manga.get('url'):
            # parse URL and populate id or rss entry
            url: str = manga['url']
            if 'https://mangadex.org/' in url:
                if match := re.match(r'^https://mangadex\.org/title/([^/?#]+)', url, flags=re.IGNORECASE):
                    manga['id'] = match.group(1)
                else:
                    click.echo(f'Malformed MangaDex URL {url} in manga entry: {manga}', err=True)
            elif 'https://mangasee123.com/' in url:
                rss = re.sub(r'^https://mangasee123\.com/manga/([^/?#]+)',
                             r'https://mangasee123.com/rss/\g<1>.xml', url, 1, re.IGNORECASE)
                if rss.endswith('.xml'):
                    manga['rss'] = rss
                else:
                    click.echo(f'Malformed MangaSee URL {url} in manga entry: {manga}', err=True)
            else:
                click.echo(f'Unsupported URL {url} in manga entry: {manga}', err=True)

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
