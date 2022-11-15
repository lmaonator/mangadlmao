import concurrent.futures
import logging
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional, Union
from urllib.parse import urlparse

import requests

from mangadlmao.cbz import create_cbz
from mangadlmao.utils import (
    ProgressCallback,
    download_cover,
    format_chapter_number,
    sanitize_path,
)

logger = logging.getLogger(__name__)


class MangaPlus:
    BASE_URL = "https://mangaplus.shueisha.co.jp"
    API_URL = "https://jumpg-webapi.tokyo-cdn.com/api"
    URL_REGEX = re.compile(
        r"^https://mangaplus.shueisha.co.jp/titles/(\d+)", re.IGNORECASE
    )
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"
    )

    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers
        self._session = requests.Session()
        self._headers = {
            "Origin": self.BASE_URL,
            "Referer": self.BASE_URL,
            "User-Agent": self.USER_AGENT,
        }
        self._session.headers.update(self._headers)
        self._last_request = 0.0

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        time.sleep(max(0.0, self._last_request + 1.0 - time.monotonic()))  # Ratelimit
        params["format"] = "json"
        with self._session.get(
            self.API_URL + endpoint, params=params, timeout=30.0
        ) as r:
            return r.json()

    def match(self, url: str) -> Union[int, None]:
        """Matches URL against MangaPlus and returns the manga ID or None"""
        if match := self.URL_REGEX.match(url):
            return int(match.group(1))
        return None

    @dataclass
    class Chapter:
        id: int
        title: str
        number: float
        datetime: datetime

    @dataclass
    class Manga:
        title: str
        author: str
        overview: str
        cover_url: str
        chapters: list["MangaPlus.Chapter"]

    def get_details(self, manga_id: int) -> Manga:
        data = self._request("/title_detail", {"title_id": manga_id})
        data = data["success"]["titleDetailView"]
        title = data["title"]

        chapters: list[MangaPlus.Chapter] = []
        for c in data["firstChapterList"] + data.get("lastChapterList", []):
            number = float(c["name"].lstrip("#"))
            chapters.append(
                MangaPlus.Chapter(
                    id=c["chapterId"],
                    title=c["subTitle"],
                    number=number,
                    datetime=datetime.fromtimestamp(
                        c["startTimeStamp"], tz=timezone.utc
                    ),
                )
            )

        return MangaPlus.Manga(
            title=title["name"],
            author=title["author"],
            overview=data["overview"],
            cover_url=title["portraitImageUrl"],
            chapters=chapters,
        )

    @dataclass
    class Page:
        number: int
        image_url: str
        encryption_key: str

    def get_pages(self, chapter_id: int) -> list[Page]:
        data = self._request(
            "/manga_viewer",
            {"chapter_id": chapter_id, "split": "no", "img_quality": "super_high"},
        )
        data = data["success"]["mangaViewer"]

        pages: list[MangaPlus.Page] = []
        for i, p in enumerate(
            (x["mangaPage"] for x in data["pages"] if "mangaPage" in x), 1
        ):
            pages.append(
                MangaPlus.Page(
                    number=i, image_url=p["imageUrl"], encryption_key=p["encryptionKey"]
                )
            )
        return pages

    def decrypt(self, encryption_key: str, image_bytes: bytes) -> bytes:
        key = bytearray.fromhex(encryption_key)
        key_size = len(key)
        return bytes(byte ^ key[i % key_size] for i, byte in enumerate(image_bytes))

    def download_page(self, page: Page, tmpdir: str) -> bool:
        filename = f"{page.number:04}{Path(urlparse(page.image_url).path).suffix}"
        filepath = Path(tmpdir, filename)

        try:
            with requests.get(page.image_url, timeout=30.0, headers=self._headers) as r:
                if not r.ok:
                    return False
                decrypted = self.decrypt(page.encryption_key, r.content)
                with filepath.open("wb") as f:
                    f.write(decrypted)
                return True
        except requests.RequestException:
            return False

    @contextmanager
    def download_chapter(self, chapter: Chapter) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            pages = self.get_pages(chapter.id)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers
            ) as executor:
                futures = [
                    executor.submit(self.download_page, page, tmpdir) for page in pages
                ]
                concurrent.futures.wait(
                    futures,
                    return_when=concurrent.futures.FIRST_EXCEPTION,
                )

            yield Path(tmpdir)

    def download_manga(
        self,
        manga_id: int,
        dest_dir: Path,
        title: Optional[str] = None,
        from_chapter: Optional[float] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        details = self.get_details(manga_id)

        if title:
            details.title = title

        dest_dir /= sanitize_path(details.title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        download_cover(details.cover_url, dest_dir, self._session)

        last_chapter = math.floor(details.chapters[-1].number)

        if from_chapter is not None:
            details.chapters = [c for c in details.chapters if c.number >= from_chapter]

        if progress_callback:
            progress_callback(length=len(details.chapters))

        for chapter in details.chapters:
            number = f"{chapter.number:g}"
            number_str = format_chapter_number(number)
            filename = sanitize_path(
                f"{number_str} - {chapter.title[:128]} [MangaPlus-{chapter.id}].cbz"
            )

            comic_info = {
                "Title": chapter.title,
                "Number": number,
                "Translator": "MangaPlus",
                "Year": chapter.datetime.year,
                "Month": chapter.datetime.month,
                "Day": chapter.datetime.day,
                "Series": details.title,
                "Count": last_chapter,
                "Writer": details.author,
            }

            filepath = dest_dir / filename
            if filepath.exists():
                logger.debug("Skipping already downloaded chapter: %s", filepath)
            else:
                try:
                    with self.download_chapter(chapter) as tmpdir:
                        create_cbz(tmpdir, filepath, comic_info)
                        os.utime(dest_dir)
                except Exception:
                    pass
            if progress_callback:
                progress_callback(progress=1)
