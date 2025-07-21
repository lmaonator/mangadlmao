import concurrent.futures
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Sequence, Union

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


class RetryException(Exception):
    pass


class MangaDex:
    BASE_URL = "https://api.mangadex.org"
    MAX_FAILED_REPORTS = 3

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers
        self.s = requests.Session()
        self.last_requests: dict[str, float] = {}
        self.failed_reports = 0
        self.report_lock = threading.Lock()

    @contextmanager
    def _request(self, method: str, url: str, *args, **kwargs):
        """Wrapper around requests.Session.request with ratelimit"""
        # Global ratelimit is 5 request per second per IP, leave some room
        time.sleep(
            max(0.0, self.last_requests.get("global", 0.0) + 0.5 - time.monotonic())
        )
        # per route ratelimits
        if (route := "/at-home/server/") in url:
            # /at-home/server/ ratelimit is 40 requests per minute, leave some room
            time.sleep(
                max(0.0, self.last_requests.get(route, 0.0) + 2 - time.monotonic())
            )
        else:
            route = ""
        try:
            with self.s.request(method, url, *args, **kwargs) as r:
                yield r
        finally:
            self.last_requests["global"] = time.monotonic()
            if route:
                self.last_requests[route] = time.monotonic()

    @dataclass
    class MangaDetails:
        title: str
        cover_url: str
        last_chapter: str
        author: str
        artist: str

    def get_manga_details(self, manga_id: str) -> MangaDetails:
        with self._request(
            "GET",
            f"{self.BASE_URL}/manga/{manga_id}",
            params={
                "includes[]": ["cover_art", "author", "artist"],
            },
        ) as r:
            data: dict[str, Any] = r.json()["data"]
            attributes: dict[str, Any] = data["attributes"]
            # title
            title: str = attributes["title"].get("en", "")
            if not title:
                title = attributes["title"].get("ja-ro", "")
            if not title:
                title = attributes["title"].get("ja", "")

            # cover
            cover_rel = next(
                (x for x in data["relationships"] if x["type"] == "cover_art")
            )
            cover_url: str = (
                f"https://uploads.mangadex.org/covers/{manga_id}/{cover_rel['attributes']['fileName']}"
            )

            last_chapter: str = attributes.get("lastChapter", "")

            author = ",".join(
                x["attributes"]["name"]
                for x in data["relationships"]
                if x["type"] == "author"
            )
            artist = ",".join(
                x["attributes"]["name"]
                for x in data["relationships"]
                if x["type"] == "artist"
            )

            return self.MangaDetails(title, cover_url, last_chapter, author, artist)

    def get_manga_chapters(
        self,
        manga_id: str,
        languages: Sequence[str],
        since: Union[datetime, date, None] = None,
    ) -> list[dict]:
        chapters = []

        params: dict[str, Any] = {
            "translatedLanguage[]": languages,
            "contentRating[]": ["safe", "suggestive", "erotica", "pornographic"],
            "includes[]": ["scanlation_group", "user"],
            "order[createdAt]": "asc",
        }
        if since is not None:
            # convert date to datetime
            if not isinstance(since, datetime):
                since = datetime(
                    since.year, since.month, since.day, tzinfo=timezone.utc
                )
            params["updatedAtSince"] = since.isoformat(timespec="seconds")

        limit = 500
        offset = 0
        while True:
            params.update(
                {
                    "limit": limit,
                    "offset": offset,
                }
            )
            with self._request(
                "GET", f"{self.BASE_URL}/manga/{manga_id}/feed", params=params
            ) as r:
                data = r.json()
                chapters.extend(data["data"])

                offset = data["limit"] + data["offset"]
                if offset >= data["total"]:
                    break

        return chapters

    def at_home_chapter(self, chapter_id: str):
        # https://api.mangadex.org/docs/reading-chapter/
        with self._request("GET", f"{self.BASE_URL}/at-home/server/{chapter_id}") as r:
            chapter_data = r.json()
        return chapter_data

    def at_home_download_page(
        self, url_prefix: str, page: str, tmpdir: str, page_number: Optional[int] = None
    ) -> bool:
        page_number_str = "" if page_number is None else f"{page_number:03}-"
        url = url_prefix + page
        num_bytes = 0
        logger.debug("Downloading chapter page %s from URL %s", page, url)
        start_time = time.monotonic()
        try:
            with requests.get(url, timeout=30, stream=True) as r:
                with open(Path(tmpdir, page_number_str + Path(page).name), "wb") as fd:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        fd.write(chunk)
                        num_bytes = fd.tell()
                try:
                    self.at_home_report(True, url, num_bytes, start_time, r)
                except requests.RequestException:
                    pass
                return True
        except requests.RequestException as e:
            self.at_home_report(False, url, num_bytes, start_time, e.response)
            return False

    def at_home_report(
        self,
        success: bool,
        url: str,
        num_bytes: int,
        start_time: float,
        response: Optional[requests.Response],
    ):
        # https://api.mangadex.org/docs/reading-chapter/
        if "mangadex.org" in url:
            return True
        with self.report_lock:
            if self.failed_reports > self.MAX_FAILED_REPORTS:
                logger.debug("Skipping MangaDex@Home report due to repeated failures")
                return True

        duration = round((time.monotonic() - start_time) * 1000)

        if response is not None:
            cached = response.headers.get("X-Cache", "").startswith("HIT")
        else:
            cached = False

        logger.debug(
            "Reporting to MangaDex@Home: url: %s, success: %s, cached: %s, bytes: %s, duration: %s",
            url,
            success,
            cached,
            num_bytes,
            duration,
        )

        try:
            with requests.post(
                "https://api.mangadex.network/report",
                json={
                    "url": url,
                    "success": success,
                    "cached": cached,
                    "bytes": num_bytes,
                    "duration": duration,
                },
                timeout=30,
            ) as r:
                with self.report_lock:
                    if r.ok and self.failed_reports > 0:
                        self.failed_reports -= 1
                    else:
                        self.failed_reports += 1
                return r.ok
        except requests.RequestException:
            logger.debug(
                "Exception while reporting to MangaDex@Home for url: %s",
                url,
                exc_info=True,
            )
            with self.report_lock:
                self.failed_reports += 1
            return False

    @contextmanager
    def download_chapter(self, chapter_id: str):
        # https://api.mangadex.org/docs/reading-chapter/
        chapter_data = self.at_home_chapter(chapter_id)
        base_url = chapter_data["baseUrl"]
        chapter_hash = chapter_data["chapter"]["hash"]
        pages: list[str] = chapter_data["chapter"]["data"]
        logger.debug(
            "Downloading chapter %s with hash %s from baseUrl %s",
            chapter_id,
            chapter_hash,
            base_url,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            _attempts = 0
            for _attempts in range(3):
                failed_pages = []
                url_prefix = f"{base_url}/data/{chapter_hash}/"

                # download in parallel
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.max_workers
                ) as executor:
                    # submit and save a mapping future -> page
                    future_to_page: dict[concurrent.futures.Future[bool], str] = {}
                    for index, page in enumerate(pages):
                        future_to_page[
                            executor.submit(
                                self.at_home_download_page,
                                url_prefix,
                                page,
                                tmpdir,
                                index + 1,
                            )
                        ] = page
                    # iterate over results as they are completed, getting the original page from mapping
                    for future in concurrent.futures.as_completed(future_to_page):
                        page = future_to_page[future]
                        try:
                            fr = future.result()
                        except Exception:
                            fr = False
                        if not fr:
                            failed_pages.append(page)

                if failed_pages:
                    logger.debug(
                        "Retrying to download failed pages: %s", ", ".join(failed_pages)
                    )
                    # get new base url to download from different node
                    base_url = self.at_home_chapter(chapter_id)["baseUrl"]
                    # replace pages list with list of failed pages
                    pages = failed_pages
                    continue
                else:
                    break
            else:  # no break occurred, max retries reached
                logger.debug(
                    "Aborting chapter download after %s attempts", _attempts + 1
                )
                raise RetryException()

            yield Path(tmpdir)

    def download_manga(
        self,
        manga_id: str,
        manga_title: str,
        languages: Sequence[str],
        exclude: Sequence[str],
        dest_dir: Path,
        since: Union[datetime, date, Literal["auto"], None] = None,
        progress_callback: Optional[ProgressCallback] = None,
        from_chapter: Optional[float] = None,
    ):
        details = self.get_manga_details(manga_id)

        # override with user provided title
        if manga_title:
            details.title = manga_title

        # prepare destination directory
        dest_dir /= sanitize_path(details.title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # download cover
        download_cover(details.cover_url, dest_dir, self.s)

        if since == "auto":
            if (most_recent := most_recent_modified(dest_dir)) is None:
                most_recent = datetime.fromtimestamp(0, tz=timezone.utc)
            since_dt = most_recent
        # convert date to datetime
        elif isinstance(since, date) and not isinstance(since, datetime):
            since_dt = datetime(since.year, since.month, since.day).astimezone()
        elif isinstance(since, datetime):
            since_dt = since
        else:
            since_dt = datetime.fromtimestamp(0, tz=timezone.utc)

        chapters = self.get_manga_chapters(manga_id, languages)
        if progress_callback:
            progress_callback(length=len(chapters))

        def progress_update(chapter: Optional[str] = None):
            if progress_callback:
                progress_callback(progress=1, chapter=chapter)

        # caseless exclude comparison
        exclude = [e.casefold() for e in exclude]

        for index, chapter in enumerate(chapters):
            scanlation_group: str = ""
            user: str = ""
            skip_excluded = False
            for r in chapter["relationships"]:
                if r["type"] == "scanlation_group":
                    scanlation_group = r["attributes"]["name"]
                    # skip excluded scanlation groups
                    if any(
                        x in (scanlation_group.casefold(), r["id"].casefold())
                        for x in exclude
                    ):
                        logger.info(
                            'Skipping chapter from excluded scanlation group "%s" with ID %s',
                            scanlation_group,
                            r["id"],
                        )
                        skip_excluded = True
                elif r["type"] == "user":
                    user = r["attributes"]["username"]
                    # skip excluded users
                    if any(x in (user.casefold(), r["id"].casefold()) for x in exclude):
                        logger.info(
                            'Skipping chapter from excluded user "%s" with ID %s',
                            user,
                            r["id"],
                        )
                        skip_excluded = True
            if skip_excluded:
                progress_update()
                continue

            translator = scanlation_group if scanlation_group else user

            chapter_id = chapter["id"]
            a: dict[str, Any] = chapter["attributes"]

            # skip chapters updated before <since>
            updated = datetime.fromisoformat(a["updatedAt"])
            if since_dt >= updated:
                progress_update()
                continue

            # skip external chapters (MangaPlus, etc.)
            if a["externalUrl"]:
                logger.info(
                    'Skipping external chapter "%s" by "%s". Chapter ID: %s - URL: %s',
                    a["title"],
                    translator,
                    chapter_id,
                    a["externalUrl"],
                )
                progress_update()
                continue

            # if chapter has no number, guess it based on its position in the list
            if (chapter_number := a["chapter"]) is None:
                # get previous chapter number and distances to it (in case there are multiple numberless)
                distance: int = 0
                for i in range(index - 1, -1, -1):
                    if (
                        chapter_number := chapters[i]["attributes"]["chapter"]
                    ) is not None:
                        distance = index - i
                        break
                # if there was no previous chapter, try to get the following chapter number
                if chapter_number is None:
                    for i in range(index + 1, len(chapters)):
                        if (
                            chapter_number := chapters[i]["attributes"]["chapter"]
                        ) is not None:
                            distance = index - i
                            break
                if chapter_number is not None:
                    # try to offset chapter number based on distance, minimum 0.1 and maximum 0.9
                    offset = min(0.9, max(0.1, 0.1 * distance))
                    try:
                        chapter_number = f"{float(chapter_number) + offset:g}"
                    except ValueError:
                        # do nothing if found number was not a number, just use it as is
                        pass
                else:
                    logger.warning(
                        'Chapter with title "%s" by "%s" has no chapter number and guessing failed. '
                        "Chapter ID: %s - Guessed number: %s - Distance: %s",
                        a["title"],
                        translator,
                        chapter_id,
                        chapter_number,
                        distance,
                    )
                    progress_update(f"{chapter_number} by {translator}")
                    continue

            try:
                if from_chapter is not None and float(chapter_number) < from_chapter:
                    logger.debug(
                        'Skipping chapter %s with title "%s" by "%s" due to "from: %s"',
                        chapter_number,
                        a["title"],
                        translator,
                        from_chapter,
                    )
                    progress_update(f"{chapter_number} by {translator}")
                    continue
            except ValueError:
                # don't skip if chapter_number is not a number
                pass

            created = datetime.fromisoformat(a["createdAt"])
            comic_info = {
                "Title": a["title"],
                "Number": chapter_number,
                "Translator": translator,
                "LanguageISO": a["translatedLanguage"],
                "Year": updated.year,
                "Month": updated.month,
                "Day": updated.day,
                "Series": details.title,
                "Count": details.last_chapter,
                "Writer": details.author,
                "Penciller": details.artist,
            }
            number = format_chapter_number(str(chapter_number))
            filename = sanitize_path(
                f"{number} - {translator} {chapter_id} {created:%Y-%m-%d %H-%M-%S}.cbz"
            )
            filepath = dest_dir / filename
            if filepath.exists() and filepath.stat().st_mtime >= updated.timestamp():
                logger.debug("Skipping already downloaded chapter: %s", filepath)
                progress_update(f"{chapter_number} by {translator}")
                continue
            try:
                with self.download_chapter(chapter_id) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
                    os.utime(filepath, (updated.timestamp(), updated.timestamp()))
                    # set modified time of directory to force a mergerfs cache update
                    # and prompt Komga to scan it
                    os.utime(dest_dir)
            except (RetryException, requests.RequestException) as e:
                logger.warn(
                    'Download of chapter with title "%s" by "%s" failed: %s',
                    a["title"],
                    translator,
                    e,
                )
                return
            else:
                # delete duplicate chapters with the same ID
                for f in dest_dir.glob(f"* {chapter_id} *.cbz"):
                    if f != filepath:
                        logger.info(
                            "Deleting duplicate chapter with the same ID: %s", f
                        )
                        f.unlink(missing_ok=True)
            finally:
                progress_update(f"{chapter_number} by {translator}")
