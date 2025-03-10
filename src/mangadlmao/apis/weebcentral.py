import concurrent.futures
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Union

import filetype
import requests
from bs4 import BeautifulSoup

from mangadlmao.cbz import create_cbz
from mangadlmao.utils import (
    ProgressCallback,
    download_cover,
    format_chapter_number,
    most_recent_modified,
    sanitize_path,
)

logger = logging.getLogger(__name__)


@dataclass
class Chapter:
    id: str
    url: str
    title: str
    num: str
    dt: datetime


class WeebCentral:
    DOMAIN = "https://weebcentral.com/"
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers
        self.s = requests.Session()
        self.s.headers["User-Agent"] = self.UA

    def download_manga(
        self,
        series_id: str,
        manga_title: str,
        dest_dir: Path,
        since: Union[datetime, date, Literal["auto"], None] = None,
        progress_callback: Optional[ProgressCallback] = None,
        from_chapter: Optional[float] = None,
    ):
        try:
            with self.s.get(self.DOMAIN + f"series/{series_id}", timeout=30.0) as r:
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
        except requests.RequestException:
            logger.error("Failed to get WeebCentral series page", exc_info=True)
            return

        if not manga_title:
            manga_title = soup.find("h1").string  # pyright: ignore

        # prepare destination directory
        dest_dir /= sanitize_path(manga_title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # download cover
        if img := soup.find("meta", {"property": "og:image"}):
            img_url: str
            if img_url := img.get(  # pyright: ignore reportAttributeAccessIssue
                "content"
            ):
                download_cover(img_url, dest_dir, self.s)

        if since == "auto":
            since_dt = most_recent_modified(dest_dir)
        # convert date to datetime
        elif isinstance(since, date) and not isinstance(since, datetime):
            since_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
        elif isinstance(since, datetime):
            since_dt = since
        else:
            since_dt = None

        chapters = self.get_chapters(series_id)

        if progress_callback:
            progress_callback(length=len(chapters))

        # download chapters
        def progress_update(chapter: Optional[str] = None):
            if progress_callback:
                progress_callback(progress=1, chapter=chapter)

        for chapter in reversed(chapters):
            progress_update(chapter.num)

            try:
                if from_chapter is not None and float(chapter.num) < from_chapter:
                    logger.debug(
                        'Skipping chapter %s due to "from: %s"',
                        chapter.num,
                        from_chapter,
                    )
                    continue
            except ValueError:
                # don't skip if chapter.num is not a number
                pass

            # skip chapters updated before <since>
            if since_dt is not None:
                if since_dt >= chapter.dt:
                    # chapter was updated before since, skip
                    logger.debug(
                        "Skipping chapter %s because it is older than %s (%s)",
                        chapter.num,
                        since_dt,
                        chapter.dt,
                    )
                    continue

            comic_info = {
                "Title": chapter.title,
                "Number": chapter.num,
                "Translator": "WeebCentral",
                "Series": manga_title,
                "LanguageISO": "en",
                "Year": chapter.dt.year,
                "Month": chapter.dt.month,
                "Day": chapter.dt.day,
            }
            published_str = chapter.dt.strftime("%Y-%m-%d %H-%M-%S")
            number = format_chapter_number(chapter.num)
            filename = sanitize_path(f"{number} - WeebCentral {published_str}.cbz")
            filepath = dest_dir / filename
            if filepath.exists() and filepath.stat().st_mtime >= chapter.dt.timestamp():
                logger.debug("Skipping already downloaded chapter: %s", filepath)
                continue
            try:
                with self.download_chapter(chapter.url) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
                    os.utime(filepath, (chapter.dt.timestamp(), chapter.dt.timestamp()))
                    # set modified time of directory to force a mergerfs cache update
                    # and prompt Komga to scan it
                    os.utime(dest_dir)
            except (requests.RequestException, Exception) as e:
                logger.error(
                    'Download of chapter with title "%s" failed: %s', chapter.title, e
                )
                return
            finally:
                time.sleep(1)

    def get_chapters(self, series_id: str):
        chapters: list[Chapter] = []
        with self.s.get(self.DOMAIN + f"series/{series_id}/full-chapter-list") as r:
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for div in soup.findChildren("div"):
                title = div.find("span", {"class": ""}).string
                if m := re.search(r"(\d+\.?\d*)", title):
                    num = m.group(1)
                else:
                    num = ""
                c = Chapter(
                    id=div.input.get("value"),
                    url=div.a.get("href"),
                    title=title,
                    num=num,
                    dt=datetime.fromisoformat(div.time.get("datetime")),
                )
                chapters.append(c)
        return chapters

    @contextmanager
    def download_chapter(self, chapter_url: str):
        try:
            chapter_url += (
                "/images?is_prev=False&current_page=1&reading_style=long_strip"
            )
            with self.s.get(chapter_url, timeout=30.0) as r:
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                pages = (img.get("src") for img in soup.find_all("img"))
                # download pages
                with tempfile.TemporaryDirectory() as tmpdir:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=self.max_workers
                    ) as executor:
                        futures = []
                        for page, url in enumerate(pages, 1):
                            futures.append(
                                executor.submit(self.download, url, page, tmpdir)
                            )
                        done, _ = concurrent.futures.wait(
                            futures, return_when=concurrent.futures.FIRST_EXCEPTION
                        )
                        for f in done:
                            e = f.exception()
                            if e is not None:
                                raise e

                    yield Path(tmpdir)
        finally:
            pass

    def download(self, page_url: str, page_number: int, tmpdir: str):
        with requests.get(
            page_url,
            timeout=30,
            stream=True,
            headers={"Referer": self.DOMAIN, "User-Agent": self.UA},
        ) as page_result:
            page_result.raise_for_status()
            ext = page_url.rsplit(".", maxsplit=1)[1]
            guessed = False
            filepath = Path(tmpdir, f"{page_number:03d}")
            with filepath.open("wb") as image_file:
                for chunk in page_result.iter_content(chunk_size=64 * 1024):
                    if not guessed:
                        guessed = True
                        if guess := filetype.guess_extension(chunk):
                            ext = guess
                    image_file.write(chunk)
            filepath.rename(filepath.with_suffix("." + ext))
