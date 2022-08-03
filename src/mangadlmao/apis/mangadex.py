import logging
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Union

import requests
from mangadlmao.cbz import create_cbz
from mangadlmao.utils import format_chapter_number, sanitize_path

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

    def get_manga_title_and_cover(self, manga_id: str) -> tuple[str, BytesIO]:
        with self._request('GET', f"{self.BASE_URL}/manga/{manga_id}", params={
            'includes[]': ['cover_art'],
        }) as r:
            data = r.json()['data']
            # title
            title = data['attributes']['title'].get('en')
            if not title:
                title = data['attributes']['title'].get('ja-ro')

            # cover
            cover_rel = next((x for x in data['relationships'] if x['type'] == 'cover_art'), None)
            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{cover_rel['attributes']['fileName']}"
            with self.s.get(cover_url) as r:
                cover = BytesIO(r.content)
                cover.name = 'cover.' + cover_url.rsplit('.', 1)[1]

            return (title, cover)

    def get_manga_chapters(self, manga_id: str, languages: list[str],
                           since: Union[datetime, date] = None) -> list[dict]:
        chapters = []

        params = {
            'translatedLanguage[]': languages,
            'contentRating[]': ['safe', 'suggestive', 'erotica', 'pornographic'],
            'includes[]': ['scanlation_group', 'user'],
        }
        if since is not None:
            # convert date to datetime
            if not isinstance(since, datetime):
                since = datetime(since.year, since.month, since.day)
            params['createdAtSince'] = since.isoformat(timespec='seconds')

        limit = 500
        offset = 0
        while True:
            params.update({
                'limit': limit,
                'offset': offset,
            })
            with self._request('GET', f"{self.BASE_URL}/manga/{manga_id}/feed", params=params) as r:
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

    def download_manga(self, manga_id: str, manga_title: str, languages: list[str], dest_dir: Path = Path('.'),
                       since: datetime = None):
        # get title and cover URL
        series_title, cover = self.get_manga_title_and_cover(manga_id)

        # override with user provided title
        if manga_title:
            series_title = manga_title

        # prepare destination directory
        dest_dir /= sanitize_path(series_title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # delete old covers
        for c in dest_dir.glob('cover.*'):
            c.unlink(missing_ok=True)
        # save cover
        with (dest_dir / str(cover.name)).open('wb') as f:
            f.write(cover.getbuffer())
        cover.close()

        chapters = self.get_manga_chapters(manga_id, languages, since)
        for chapter in chapters:
            scanlation_group = ''
            username = ''
            for r in chapter['relationships']:
                if r['type'] == 'scanlation_group':
                    scanlation_group = r['attributes']['name']
                elif r['type'] == 'user':
                    username = r['attributes']['username']

            author = scanlation_group if scanlation_group else username

            chapter_id = chapter['id']
            a = chapter['attributes']

            # skip external chapters (MangaPlus, etc.)
            if a['externalUrl']:
                logger.info('Skipping external chapter "%s" by "%s". Chapter ID: %s - URL: %s',
                            a['title'], author, chapter_id, a['externalUrl'])
                continue

            if a['chapter'] is None:
                logger.warning(
                    'Chapter with title "%s" by "%s" has no chapter number, skipping. Chapter ID: %s',
                    a['title'], author, chapter_id)
                continue

            comic_info = {
                'Title': a['title'],
                'Number': a['chapter'],
                'Translator': author,
                'Series': series_title,
                'LanguageISO': a['translatedLanguage'],
            }
            updated = str(a['updatedAt']).replace(':', '-').split('+', 1)[0]
            number = format_chapter_number(str(a['chapter']))
            filename = sanitize_path(f"{number} - {author} {chapter_id} {updated}.cbz")
            filepath = dest_dir / filename
            if filepath.exists():
                logger.debug('Skipping already downloaded chapter: %s', filepath)
                continue
            try:
                with self.download_chapter(chapter_id) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
            except RetryException:
                pass
