import json
import logging
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from time import mktime, strftime

import feedparser
import requests
from mangadlmao.cbz import create_cbz
from mangadlmao.utils import format_chapter_number, sanitize_path

logger = logging.getLogger(__name__)


class MangaSee:
    def __init__(self) -> None:
        self.s = requests.Session()

    def download_manga(self, rss_url: str, manga_title: str = "", dest_dir: Path = Path('.'),
                       since: datetime = None):
        try:
            with self.s.get(rss_url, timeout=30.0) as r:
                d = feedparser.parse(r.text)
        except requests.RequestException:
            return

        if not manga_title:
            manga_title = d.feed.title

        # prepare destination directory
        dest_dir /= sanitize_path(manga_title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # download cover
        try:
            with self.s.get(d.feed.image.url, timeout=30.0) as r:
                if r.ok:
                    cover_name = 'cover.' + d.feed.image.url.rsplit('.', 1)[1]
                    # delete old covers
                    for c in dest_dir.glob('cover.*'):
                        c.unlink(missing_ok=True)
                    # save cover
                    with (dest_dir / str(cover_name)).open('wb') as f:
                        f.write(r.content)
        except Exception:
            pass

        # convert date to datetime
        if isinstance(since, date) and not isinstance(since, datetime):
            since = datetime(since.year, since.month, since.day)

        # download chapters
        for entry in d.entries:
            # skip chapters updated before <since>
            if since is not None:
                updated = datetime.fromtimestamp(mktime(entry.updated_parsed))
                if since >= updated:
                    # chapter was updated before since, skip
                    continue

            number = entry.guid.split("-")[-1]

            comic_info = {
                'Title': entry.title,
                'Number': number,
                'Translator': 'MangaSee',
                'Series': manga_title,
                'LanguageISO': 'en',
            }
            updated = strftime('%Y-%m-%dT%H-%M-%S', entry.updated_parsed)
            number = format_chapter_number(number)
            filename = sanitize_path(f"{number} - MangaSee {updated}.cbz")
            filepath = dest_dir / filename
            if filepath.exists():
                logger.debug('Skipping already downloaded chapter: %s', filepath)
                continue
            try:
                with self.download_chapter(entry.link) as tmpdir:
                    create_cbz(tmpdir, filepath, comic_info)
            except Exception:
                pass

    @contextmanager
    def download_chapter(self, chapter_url: str, since: datetime = None):
        try:
            with self.s.get(chapter_url, timeout=30.0) as r:
                m = re.search(r'\n\s+vm\.CurChapter = ({.+});\r?\n', r.text)
                cur_chapter = json.loads(m.group(1))
                domain = re.search(r'\n\s+vm\.CurPathName = \"(.+)\";\r?\n', r.text).group(1)
                index_name = re.search(r'\n\s+vm\.IndexName = \"(.+)\";\r?\n', r.text).group(1)

                # convert Chapter string to formatted chapter number:
                #   100010 -> chapter 0001
                #   100165 -> chapter 0016.5
                number = cur_chapter['Chapter'][1:]
                if number[-1] != "0":
                    number = number[:-1] + '.' + number[-1]
                else:
                    number = number[:-1]
                number = format_chapter_number(number, 4)

                # download pages
                with tempfile.TemporaryDirectory() as tmpdir:
                    for page in range(1, int(cur_chapter['Page']) + 1):
                        url = f"https://{domain}/manga/{index_name}/{number}-{page:03d}.png"

                        with self.s.get(url, stream=True) as page_result:
                            with Path(tmpdir, f"{page:03d}.png").open('wb') as image_file:
                                for chunk in page_result.iter_content(chunk_size=64 * 1024):
                                    image_file.write(chunk)

                    yield Path(tmpdir)
        finally:
            pass
