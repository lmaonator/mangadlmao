import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Protocol, Union

import requests


def sanitize_path(path: Union[str, Path]):
    # make path component windows compatible
    return (
        str(path)
        .lstrip(". ")
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "")
        .replace("?", "")
        .replace("*", "")
        .replace("|", "")
        .replace("<", "_")
        .replace(">", "_")
        .replace('"', "'")
        .strip()
    )


def format_chapter_number(number: str, count: int = 3, char: str = "0"):
    """
    Format chapter number.
    - 25.5 -> 025.5
    - 3 -> 003
    """
    if (i := number.find(".")) > 0:
        integer_portion = number[:i]
    else:
        integer_portion = number
    num = count - len(integer_portion)
    return char * num + number


def download_cover(
    url: str, dest_dir: Path, session: Optional[requests.Session] = None
):
    get = session.get if session is not None else requests.get
    try:
        with get(url, timeout=30.0, stream=True) as r:
            if r.ok:
                # parse last-modified time to timestamp
                try:
                    modified = datetime.strptime(
                        r.headers["last-modified"], "%a, %d %b %Y %H:%M:%S GMT"
                    ).timestamp()
                except (KeyError, ValueError):
                    modified = (datetime.now() - timedelta(days=365)).timestamp()

                cover_name: str = "cover." + url.rsplit(".", 1)[1]
                cover_path = dest_dir / cover_name

                # get file modified timestamp
                try:
                    file_modified = cover_path.stat().st_mtime
                except FileNotFoundError:
                    file_modified = 0.0

                # only save downloaded cover if it is newer
                if file_modified < modified:
                    # delete old covers
                    for c in dest_dir.glob("cover.*"):
                        c.unlink(missing_ok=True)
                    # save cover
                    with cover_path.open("wb") as f:
                        for chunk in r.iter_content(chunk_size=64 * 1024):
                            f.write(chunk)
                    # set last modified
                    os.utime(cover_path, (modified, modified))
    except requests.RequestException:
        pass


class ProgressCallback(Protocol):
    def __call__(
        self,
        progress: Optional[int] = None,
        length: Optional[int] = None,
        chapter: Optional[str] = None,
    ) -> Any:
        ...


def most_recent_modified(
    directory: Path, pattern: str = "*.cbz"
) -> Union[datetime, None]:
    """
    Returns the most recent modified time as `datetime` out of all files matching `pattern`
    in `directory` or `None` if no files matched.
    """
    mtimes = sorted(
        (x.stat().st_mtime for x in directory.glob(pattern) if x.is_file()),
        reverse=True,
    )
    return datetime.fromtimestamp(mtimes[0]) if mtimes else None
