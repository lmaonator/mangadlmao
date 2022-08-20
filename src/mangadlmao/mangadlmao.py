import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

import appdirs
import click
import yaml

from mangadlmao.apis.mangadex import MangaDex
from mangadlmao.apis.mangasee import MangaSee

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
@click.argument("url", nargs=-1)
def main(
    config: str,
    jobs: int,
    lang: tuple[str],
    exclude: tuple[str],
    url: tuple[str],
    since_opt: Optional[str],
):
    """
    Download Manga from the configuration file or URL arguments.
    """
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
        click.echo("No manga in configuration file and no URL argument given.")
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
            cfg["since"] = datetime.fromisoformat(since_opt)
        except ValueError:
            click.secho(f"option error: '{since_opt}' is not a valid date", fg="red")
            return -1

    md = MangaDex(max_workers=jobs)
    ms = MangaSee(max_workers=jobs)
    manga: dict[str, Any]
    for manga in cfg["manga"]:
        if manga.get("url"):
            # parse URL and populate id or rss entry
            md_url: str = manga["url"]
            if "https://mangadex.org/" in md_url:
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
            elif "https://mangasee123.com/" in md_url:
                rss = re.sub(
                    r"^https://mangasee123\.com/manga/([^/?#]+)",
                    r"https://mangasee123.com/rss/\g<1>.xml",
                    md_url,
                    1,
                    re.IGNORECASE,
                )
                if rss.endswith(".xml"):
                    manga["rss"] = rss
                else:
                    click.secho(
                        f"Malformed MangaSee URL {md_url} in manga entry: {click.style(manga, fg='red')}",
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
        sdldir = click.style(download_dir, fg="magenta")
        since: Union[datetime, Literal["auto"], None] = manga.get("since", cfg["since"])

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

        if "id" in manga:
            # MangaDex
            lang = default_languages if not manga.get("lang") else manga["lang"]
            manga_exclude = global_exclude + manga.get("exclude", [])

            click.echo(
                f"Downloading MangaDex manga {stitle} ({click.style(manga['id'], fg='cyan')}) in languages"
                f" {click.style(', '.join(lang), fg='green')} to {sdldir}"
            )
            with click.progressbar(
                length=1000, item_show_func=lambda n: f"Chapter {n}" if n else None
            ) as bar:  # type: ignore[misc]  # mypy can't infer type for non-existent iterable
                md.download_manga(
                    manga["id"],
                    manga.get("title", ""),
                    lang,
                    manga_exclude,
                    download_dir,
                    since=since,
                    progress_callback=get_bar_callback(bar),
                )
        elif "rss" in manga:
            # MangaSee
            click.echo(
                f"Downloading MangaSee manga {stitle} ({click.style(manga['rss'], fg='cyan')}) to {sdldir}"
            )
            with click.progressbar(
                length=1000, item_show_func=lambda n: f"Chapter {n}" if n else None
            ) as bar:  # type: ignore[misc]  # mypy can't infer type for non-existent iterable
                ms.download_manga(
                    manga["rss"],
                    manga.get("title", ""),
                    download_dir,
                    since=since,
                    progress_callback=get_bar_callback(bar),
                )
