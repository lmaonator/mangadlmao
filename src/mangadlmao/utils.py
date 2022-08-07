import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Protocol, Union

import requests


def sanitize_path(path: Union[str, Path]):
    # make path component windows compatible
    return str(path).lstrip(". ").replace("/", "-").replace("\\", "-") \
        .replace(":", "").replace("?", "").replace("*", "").replace("|", "") \
        .replace("<", "_").replace(">", "_").replace("\"", "'").strip()


def format_chapter_number(number: str, count: int = 3, char: str = "0"):
    """
    Format chapter number.
    - 25.5 -> 025.5
    - 3 -> 003
    """
    if (i := number.find('.')) > 0:
        integer_portion = number[:i]
    else:
        integer_portion = number
    num = count - len(integer_portion)
    return char * num + number


def download_cover(url: str, dest_dir: Path, session: requests.Session = None):
    s = session if session is not None else requests
    try:
        with s.get(url, timeout=30.0) as r:
            if r.ok:
                # parse last-modified time to timestamp
                try:
                    modified = datetime.strptime(r.headers.get('last-modified'),
                                                 '%a, %d %b %Y %H:%M:%S GMT').timestamp()
                except ValueError:
                    modified = (datetime.now() - timedelta(days=365)).timestamp()

                cover_name: str = 'cover.' + url.rsplit('.', 1)[1]
                cover_path = dest_dir / cover_name

                # get file modified timestamp
                try:
                    file_modified = cover_path.stat().st_mtime
                except FileNotFoundError:
                    file_modified = 0.0

                # only save downloaded cover if it is newer
                if file_modified < modified:
                    # delete old covers
                    for c in dest_dir.glob('cover.*'):
                        c.unlink(missing_ok=True)
                    # save cover
                    with cover_path.open('wb') as f:
                        f.write(r.content)
                    # set last modified
                    os.utime(cover_path, (modified, modified))
    except requests.RequestException:
        pass


class ProgressCallback(Protocol):
    def __call__(self, progress: Optional[str] = None, chapter: Optional[str]
                 = None, length: Optional[int] = None) -> Any: ...
