import logging
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from mangadlmao.cbz import create_cbz
from mangadlmao.utils import sanitize_path

logger = logging.getLogger(__name__)


class RetryException(Exception):
    pass


class MangaDex:
    BASE_URL = "https://api.mangadex.org"

    RATELIMIT = 0.5
    """ Global ratelimit is 5 request per second per IP, leave some room for other apps """

    def __init__(self) -> None:
        self.s = requests.Session()
        self.last_request = 0.0

    @contextmanager
    def _request(self, *args, **kwargs):
        """ Wrapper around requests.Session.request with ratelimit """
        time.sleep(max(0.0, self.last_request + self.RATELIMIT - time.monotonic()))
        try:
            with self.s.request(*args, **kwargs) as r:
                yield r
        finally:
            self.last_request = time.monotonic()

    def get_manga_chapters(self, manga_id: str) -> list[dict]:
        chapters = []

        limit = 500
        offset = 0
        while True:
            with self._request('GET', f"{self.BASE_URL}/manga/{manga_id}/feed", params={
                'limit': limit,
                'offset': offset,
                'translatedLanguage[]': ['en', 'de'],
                'contentRating[]': ['safe', 'suggestive', 'erotica', 'pornographic'],
                'includes[]': ['scanlation_group', 'manga', 'user'],
            }) as r:
                data = r.json()
                chapters.extend(data['data'])

                offset = data['limit'] + data['offset']
                if offset >= data['total']:
                    break

        return chapters

    def at_home_chapter(self, chapter_id: str):
        # https://api.mangadex.org/docs/reading-chapter/
        with self._request('GET', f'{self.BASE_URL}/at-home/server/{chapter_id}') as r:
            chapter_data = r.json()
        return chapter_data

    def at_home_download_page(self, url: str, page: str, tmpdir: str):
        num_bytes = 0
        try:
            logger.debug('Downloading chapter page %s from URL %s', page, url)
            start_time = time.monotonic()
            with self.s.get(url, stream=True) as r:
                with open(Path(tmpdir, Path(page).name), 'wb') as fd:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        fd.write(chunk)
                        num_bytes = fd.tell()
                success = True
                response = r
        except requests.RequestException as e:
            success = False
            response = e.response
        finally:
            self.at_home_report(success, url, num_bytes, start_time, response)
        return success

    def at_home_report(self, success: bool, url: str, num_bytes: int, start_time: float, response: requests.Response):
        # https://api.mangadex.org/docs/reading-chapter/
        if 'mangadex.org' in url:
            return True

        duration = round((time.monotonic() - start_time) * 1000)

        if response is not None:
            cached = response.headers.get('X-Cache', '').startswith('HIT')
        else:
            cached = False

        logger.debug('Reporting to MangaDex@Home: url: %s, success: %s, cached: %s, bytes: %s, duration: %s',
                     url, success, cached, num_bytes, duration)

        with self.s.post('https://api.mangadex.network/report', json={
            'url': url,
            'success': success,
            'cached': cached,
            'bytes': num_bytes,
            'duration': duration,
        }) as r:
            return r.ok

    @contextmanager
    def download_chapter(self, chapter_id: str):
        # https://api.mangadex.org/docs/reading-chapter/
        chapter_data = self.at_home_chapter(chapter_id)
        base_url = chapter_data['baseUrl']
        chapter_hash = chapter_data['chapter']['hash']
        pages: list[str] = chapter_data['chapter']['data']
        logger.debug('Downloading chapter %s with hash %s from baseUrl %s', chapter_id, chapter_hash, base_url)

        with tempfile.TemporaryDirectory() as tmpdir:
            attempts = 0
            while True:
                if attempts >= 3:
                    logging.debug('Aborting chapter download after %s attempts', attempts)
                    raise RetryException()
                attempts += 1
                failed_pages = []
                # iterate and append failed pages to new list
                for page in pages:
                    url = '/'.join((base_url, 'data', chapter_hash, page))
                    if not self.at_home_download_page(url, page, tmpdir):
                        failed_pages.append(page)

                if failed_pages:
                    logger.debug('Retrying to download failed pages: %s', ', '.join(failed_pages))
                    # get new base url to download from different node
                    base_url = self.at_home_chapter(chapter_id)['baseUrl']
                    # replace pages list with list of failed pages
                    pages = failed_pages
                    continue
                else:
                    break

            yield Path(tmpdir)

    def download_manga(self, manga_id: str, manga_title: str, dest_dir: Path = Path('.')):
        chapters = self.get_manga_chapters(manga_id)
        for chapter in chapters:
            series_title = ''
            scanlation_group = ''
            username = ''
            for r in chapter['relationships']:
                if r['type'] == 'manga':
                    series_title = r['attributes']['title']['en']
                elif r['type'] == 'scanlation_group':
                    scanlation_group = r['attributes']['name']
                elif r['type'] == 'user':
                    username = r['attributes']['username']

            series_title = manga_title if manga_title else series_title
            author = scanlation_group if scanlation_group else username

            chapter_id = chapter['id']
            a = chapter['attributes']

            if not a['chapter']:
                logger.warning(
                    'Chapter with title "%s" by "%s" has no chapter number, skipping. Chapter ID: %s',
                    a['title'], author, chapter_id)
                continue

            comic_info = {
                'Title': a['title'],
                'Number': a['chapter'],
                'Series': series_title,
            }
            filename = sanitize_path(f"{a['chapter']:03d} - {author} - {chapter_id} - {a['updatedAt']}.cbz")
            filepath = dest_dir / sanitize_path(series_title)
            filepath.mkdir(parents=True, exist_ok=True)
            filepath /= filename
            if filepath.exists():
                logger.debug('Skipping already downloaded chapter: %s', filepath)
                continue
            try:
                with self.download_chapter(chapter_id) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
            except RetryException:
                pass
