import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

import appdirs
import click
import yaml

from mangadlmao.apis.mangadex import MangaDex
from mangadlmao.apis.mangaplus import MangaPlus
from mangadlmao.apis.weebcentral import WeebCentral

from .utils import ProgressCallback

if TYPE_CHECKING:
    from click._termui_impl import ProgressBar

APPNAME = "mangadlmao"
CONFIG_DIR = Path(appdirs.user_config_dir(APPNAME))
CONFIG_FILE = CONFIG_DIR / "config.yml"
DEFAULT_CONFIG: dict[str, Any] = {
    "download_directory": ".",
    "since": "auto",
    "lang": ["en"],
    "manga": [],
}


@click.command()
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="More verbose messages",
)
@click.option(
    "-c",
    "--config",
    is_flag=False,
    flag_value="",
    default=CONFIG_FILE,
    type=click.Path(),
    help="Print or set configuration file path.",
)
@click.option(
    "-j",
    "--jobs",
    default=4,
    show_default=True,
    help="Number of parallel chapter page downloads.",
)
@click.option(
    "-l",
    "--lang",
    multiple=True,
    help="Language to download when URLs are given, can be provided multiple times.",
)
@click.option(
    "-e",
    "--exclude",
    multiple=True,
    help="Scanlation groups and users to exclude, can be provided multiple times.",
)
@click.option(
    "-s",
    "--since",
    "since_opt",
    default=None,
    help="Download only chapters updated after specified date (eg.: 2022-02-22). If "
    "set to 'auto', only chapters newer than the most recent will be downloaded. If "
    "set to 'null' (default), all chapters will be downloaded.",
)
@click.option(
    "-f",
    "--from",
    "from_opt",
    default=None,
    type=float,
    help="Download only chapters starting from provided number",
)
@click.argument("url", nargs=-1)
def main(
    config: str,
    jobs: int,
    lang: tuple[str],
    exclude: tuple[str],
    url: tuple[str],
    verbose: int,
    since_opt: Optional[str],
    from_opt: Optional[float],
):
    """
    Download Manga from the configuration file or URL arguments.
    """
    log_levels = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging.basicConfig(level=log_levels[min(verbose, len(log_levels) - 1)])

    if config == "":
        click.echo(f"Configuration file: {click.style(CONFIG_FILE, fg='magenta')}")
        return
    else:
        cfg = DEFAULT_CONFIG
        try:
            with open(config) as f:
                cfg.update(yaml.safe_load(f))
        except FileNotFoundError:
            if config != CONFIG_FILE:
                raise

    download_dir = Path(cfg.get("download_directory", "."))
    if not download_dir.exists():
        click.secho(
            f"config error: download_directory does not exist: {click.style(download_dir, fg='red')}",
            fg="yellow",
            err=True,
        )
        return
    default_languages = cfg.get("lang", DEFAULT_CONFIG["lang"])

    if not url and not cfg.get("manga"):
        click.echo(
            "No manga in configuration file and no URL argument given.", err=True
        )
        return
    elif url:
        # overwrite manga list from configuration file with URL arguments
        cfg["manga"] = [{"url": x} for x in url]
        # use provided languages if set
        if lang:
            default_languages = lang

    global_exclude = cfg.get("exclude", []) + list(exclude)

    # overwrite global since with option
    if since_opt == "auto":
        cfg["since"] = "auto"
    elif since_opt == "null":
        cfg["since"] = None
    elif since_opt is not None:
        try:
            cfg["since"] = datetime.fromisoformat(since_opt).astimezone()
        except ValueError:
            click.secho(
                f"option error: '{since_opt}' is not a valid date", fg="red", err=True
            )
            return -1

    click.echo(f"Download directory: {click.style(download_dir, fg='magenta')}")

    def get_bar_callback(bar: "ProgressBar") -> ProgressCallback:
        def callback(
            progress: Optional[int] = None,
            length: Optional[int] = None,
            chapter: Optional[str] = None,
        ):
            if length is not None:
                bar.length = length
            if progress is not None:
                bar.update(progress, chapter)

        return callback

    md = MangaDex(max_workers=jobs)
    ms = WeebCentral(max_workers=jobs)
    mp = MangaPlus(max_workers=jobs)
    manga: dict[str, Any]
    for manga in cfg["manga"]:
        if manga.get("url"):
            # parse URL and populate id or url entries
            md_url: str = manga["url"]
            if "mangadex.org" in md_url:
                if match := re.match(
                    r"^https://mangadex\.org/title/([^/?#]+)",
                    md_url,
                    flags=re.IGNORECASE,
                ):
                    manga["id"] = match.group(1)
                else:
                    click.secho(
                        f"Malformed MangaDex URL {md_url} in manga entry: {click.style(manga, fg='red')}",
                        fg="yellow",
                        err=True,
                    )
            elif "mangasee123.com" in md_url:
                click.secho(
                    f"MangaSee is now WeebCentral, please update your config for manga entry: {click.style(manga, fg='red')}",
                    fg="yellow",
                    err=True,
                )
                continue
            elif "weebcentral.com" in md_url:
                if m := re.match(
                    r"https://weebcentral\.com/series/([A-Z0-9]+)/?.*",
                    md_url,
                    flags=re.IGNORECASE,
                ):
                    manga["weebcentral_id"] = m.group(1)
                else:
                    click.secho(
                        f"Malformed WeebCentral URL {md_url} in manga entry: {click.style(manga, fg='red')}",
                        fg="yellow",
                        err=True,
                    )
            elif "mangaplus.shueisha.co.jp" in md_url:
                if manga_id := mp.match(md_url):
                    manga["mangaplus_id"] = manga_id
                else:
                    click.secho(
                        f"Malformed MangaPlus URL {md_url} in manga entry: {click.style(manga, fg='red')}",
                        fg="yellow",
                        err=True,
                    )
            else:
                click.secho(
                    f"Unsupported URL {md_url} in manga entry: {click.style(manga, fg='red')}",
                    fg="yellow",
                    err=True,
                )

        stitle = click.style(manga.get("title", "without title"), fg="green")
        since: Union[datetime, Literal["auto"], None] = manga.get("since", cfg["since"])
        from_chapter: Union[float, None] = (
            from_opt if from_opt is not None else manga.get("from", None)
        )

        if "id" in manga:
            # MangaDex
            lang = default_languages if not manga.get("lang") else manga["lang"]
            manga_exclude = global_exclude + manga.get("exclude", [])

            title = f"{stitle} ({click.style(manga['id'], fg='cyan')})"
            click.echo(
                f"Downloading MangaDex manga {title} "
                f"in languages {click.style(', '.join(lang), fg='green')}"
            )
            bar: ProgressBar[str]
            with click.progressbar(
                length=1000, item_show_func=lambda n: f"Chapter {n}" if n else None
            ) as bar:
                try:
                    md.download_manga(
                        manga["id"],
                        manga.get("title", ""),
                        lang,
                        manga_exclude,
                        download_dir,
                        since=since,
                        progress_callback=get_bar_callback(bar),
                        from_chapter=from_chapter,
                    )
                except Exception as e:
                    click.secho(f"Download of {title} failed: {e}", fg="red", err=True)
        elif "weebcentral_id" in manga:
            title = f"{stitle} ({click.style(manga['weebcentral_id'], fg='cyan')})"
            click.echo(f"Downloading WeebCentral manga {title}")
            with click.progressbar(
                length=1000, item_show_func=lambda n: f"Chapter {n}" if n else None
            ) as bar:
                try:
                    ms.download_manga(
                        manga["weebcentral_id"],
                        manga.get("title", ""),
                        download_dir,
                        since=since,
                        progress_callback=get_bar_callback(bar),
                        from_chapter=from_chapter,
                    )
                except Exception as e:
                    click.secho(f"Download of {title} failed: {e}", fg="red", err=True)
        elif "mangaplus_id" in manga:
            # MangaPlus
            title = f"{stitle} ({click.style(manga['mangaplus_id'], fg='cyan')})"
            click.echo(f"Downloading MangaPlus manga {title}")
            # auto is not supported and convert date instances to datetime
            if since == "auto":
                since_mp = None
            elif isinstance(since, date) and not isinstance(since, datetime):
                since_mp = datetime(
                    since.year, since.month, since.day, tzinfo=timezone.utc
                )
            elif isinstance(since, datetime):
                since_mp = since
            else:
                since_mp = None
            with click.progressbar(length=1000) as bar:
                try:
                    mp.download_manga(
                        manga["mangaplus_id"],
                        download_dir,
                        manga.get("title"),
                        since_mp,
                        from_chapter,
                        progress_callback=get_bar_callback(bar),
                    )
                except Exception as e:
                    click.secho(f"Download of {title} failed: {e}", fg="red", err=True)
