import concurrent.futures
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime
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

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers
        self.s = requests.Session()
        self.last_requests: dict[str, float] = {}

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

    def get_manga_title_and_cover(self, manga_id: str) -> tuple[str, str]:
        with self._request(
            "GET",
            f"{self.BASE_URL}/manga/{manga_id}",
            params={
                "includes[]": ["cover_art"],
            },
        ) as r:
            data = r.json()["data"]
            # title
            title = data["attributes"]["title"].get("en")
            if not title:
                title = data["attributes"]["title"].get("ja-ro")

            # cover
            cover_rel = next(
                (x for x in data["relationships"] if x["type"] == "cover_art")
            )
            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{cover_rel['attributes']['fileName']}"

            return (title, cover_url)

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
                since = datetime(since.year, since.month, since.day)
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

    def at_home_download_page(self, url_prefix: str, page: str, tmpdir: str):
        url = url_prefix + page
        num_bytes = 0
        logger.debug("Downloading chapter page %s from URL %s", page, url)
        start_time = time.monotonic()
        try:
            with requests.get(url, timeout=30, stream=True) as r:
                with open(Path(tmpdir, Path(page).name), "wb") as fd:
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
        response: requests.Response,
    ):
        # https://api.mangadex.org/docs/reading-chapter/
        if "mangadex.org" in url:
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
                return r.ok
        except requests.RequestException:
            logger.debug(
                "Exception while reporting to MangaDex@Home for url: %s",
                url,
                exc_info=True,
            )
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
                    for page in pages:
                        future_to_page[
                            executor.submit(
                                self.at_home_download_page, url_prefix, page, tmpdir
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
        since: Union[datetime, Literal["auto"], None] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        # get title and cover URL
        series_title, cover_url = self.get_manga_title_and_cover(manga_id)

        # override with user provided title
        if manga_title:
            series_title = manga_title

        # prepare destination directory
        dest_dir /= sanitize_path(series_title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # download cover
        download_cover(cover_url, dest_dir, self.s)

        if since == "auto":
            since = most_recent_modified(dest_dir)

        chapters = self.get_manga_chapters(manga_id, languages, since)
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

            author = scanlation_group if scanlation_group else user

            chapter_id = chapter["id"]
            a = chapter["attributes"]

            # skip external chapters (MangaPlus, etc.)
            if a["externalUrl"]:
                logger.info(
                    'Skipping external chapter "%s" by "%s". Chapter ID: %s - URL: %s',
                    a["title"],
                    author,
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
                        author,
                        chapter_id,
                        chapter_number,
                        distance,
                    )
                    progress_update(f"{chapter_number} by {author}")
                    continue

            created = datetime.fromisoformat(a["createdAt"])
            updated = datetime.fromisoformat(a["updatedAt"])
            comic_info = {
                "Title": a["title"],
                "Number": chapter_number,
                "Translator": author,
                "Series": series_title,
                "LanguageISO": a["translatedLanguage"],
                "Year": updated.year,
                "Month": updated.month,
                "Day": updated.day,
            }
            number = format_chapter_number(str(chapter_number))
            filename = sanitize_path(
                f"{number} - {author} {chapter_id} {created:%Y-%m-%d %H-%M-%S}.cbz"
            )
            filepath = dest_dir / filename
            if filepath.exists() and filepath.stat().st_mtime >= updated.timestamp():
                logger.debug("Skipping already downloaded chapter: %s", filepath)
                progress_update(f"{chapter_number} by {author}")
                continue
            try:
                with self.download_chapter(chapter_id) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
                    os.utime(filepath, (updated.timestamp(), updated.timestamp()))
                    # set modified time of directory to force a mergerfs cache update
                    # and prompt Komga to scan it
                    os.utime(dest_dir)
            except RetryException:
                pass
            except requests.RequestException as e:
                logger.warn(
                    'Download of chapter with title "%s" by "%s" failed: %s',
                    a["title"],
                    author,
                    e,
                )
            progress_update(f"{chapter_number} by {author}")
