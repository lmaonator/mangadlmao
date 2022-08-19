import concurrent.futures
import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from time import mktime, strftime
from typing import Literal, Optional, Union

import feedparser
import requests
from mangadlmao.cbz import create_cbz
from mangadlmao.utils import (
    ProgressCallback,
    download_cover,
    format_chapter_number,
    most_recent_modified,
    sanitize_path,
)

logger = logging.getLogger(__name__)


class ChapterParseException(Exception):
    pass


class MangaSee:
    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers
        self.s = requests.Session()

    def download_manga(
        self,
        rss_url: str,
        manga_title: str,
        dest_dir: Path,
        since: Union[datetime, Literal["auto"], None] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        try:
            with self.s.get(rss_url, timeout=30.0) as r:
                d = feedparser.parse(r.text)
        except requests.RequestException:
            return

        if progress_callback:
            progress_callback(length=len(d.entries))

        if not manga_title:
            manga_title = d.feed.title

        # prepare destination directory
        dest_dir /= sanitize_path(manga_title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # download cover
        download_cover(d.feed.image.url, dest_dir, self.s)

        if since == "auto":
            since = most_recent_modified(dest_dir)
        # convert date to datetime
        if isinstance(since, date) and not isinstance(since, datetime):
            since = datetime(since.year, since.month, since.day)

        # download chapters
        def progress_update(chapter: Optional[str] = None):
            if progress_callback:
                progress_callback(progress=1, chapter=chapter)

        for entry in reversed(d.entries):
            chapter_number = entry.guid.split("-")[-1]
            updated = datetime.fromtimestamp(mktime(entry.updated_parsed))

            # skip chapters updated before <since>
            if since is not None:
                if since >= updated:
                    # chapter was updated before since, skip
                    progress_update(chapter_number)
                    continue

            comic_info = {
                "Title": entry.title,
                "Number": chapter_number,
                "Translator": "MangaSee",
                "Series": manga_title,
                "LanguageISO": "en",
                "Year": updated.year,
                "Month": updated.month,
                "Day": updated.day,
            }
            published_str = strftime("%Y-%m-%d %H-%M-%S", entry.published_parsed)
            number = format_chapter_number(chapter_number)
            filename = sanitize_path(f"{number} - MangaSee {published_str}.cbz")
            filepath = dest_dir / filename
            if filepath.exists() and filepath.stat().st_mtime >= updated.timestamp():
                logger.debug("Skipping already downloaded chapter: %s", filepath)
                progress_update(chapter_number)
                continue
            try:
                with self.download_chapter(entry.link) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
                    os.utime(filepath, (updated.timestamp(), updated.timestamp()))
                    # set modified time of directory to force a mergerfs cache update
                    # and prompt Komga to scan it
                    os.utime(dest_dir)
            except ChapterParseException as e:
                logger.error(
                    'Download of chapter with title "%s" failed: %s', entry.title, e
                )
            except requests.RequestException as e:
                logger.warn(
                    'Download of chapter with title "%s" failed: %s', entry.title, e
                )
            except Exception:
                pass
            progress_update(chapter_number)

    @contextmanager
    def download_chapter(self, chapter_url: str):
        try:
            with self.s.get(chapter_url, timeout=30.0) as r:
                if m := re.search(r"\n\s+vm\.CurChapter = ({.+});\r?\n", r.text):
                    cur_chapter = json.loads(m.group(1))
                else:
                    raise ChapterParseException("CurChapter not found")
                if m := re.search(r"\n\s+vm\.CurPathName = \"(.+)\";\r?\n", r.text):
                    domain = m.group(1)
                else:
                    raise ChapterParseException("CurPathName not found")
                if m := re.search(r"\n\s+vm\.IndexName = \"(.+)\";\r?\n", r.text):
                    index_name = m.group(1)
                else:
                    raise ChapterParseException("IndexName not found")

                # convert Chapter string to formatted chapter number:
                #   100010 -> chapter 0001
                #   100165 -> chapter 0016.5
                number = cur_chapter["Chapter"][1:]
                if number[-1] != "0":
                    number = number[:-1] + "." + number[-1]
                else:
                    number = number[:-1]
                number = format_chapter_number(number, 4)

                # download pages
                with tempfile.TemporaryDirectory() as tmpdir:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=self.max_workers
                    ) as executor:
                        futures = []
                        for page in range(1, int(cur_chapter["Page"]) + 1):
                            url = f"https://{domain}/manga/{index_name}/{number}-{page:03d}.png"
                            futures.append(
                                executor.submit(self.download, url, page, tmpdir)
                            )
                        concurrent.futures.wait(
                            futures, return_when=concurrent.futures.FIRST_EXCEPTION
                        )

                    yield Path(tmpdir)
        finally:
            pass

    def download(self, page_url: str, page_number: int, tmpdir: str):
        with requests.get(page_url, timeout=30, stream=True) as page_result:
            with Path(tmpdir, f"{page_number:03d}.png").open("wb") as image_file:
                for chunk in page_result.iter_content(chunk_size=64 * 1024):
                    image_file.write(chunk)
