import errno
import shutil
from pathlib import Path
from typing import Any, Union
from xml.sax.saxutils import escape
from zipfile import ZIP_STORED, ZipFile


def generate_comic_info(comic_info: dict[str, Any]):
    s = '<?xml version="1.0" encoding="UTF-8"?>\n<ComicInfo>\n'
    for key, value in comic_info.items():
        if value is not None:
            key = key[0].upper() + key[1:]
            s += f"\t<{key}>{escape(str(value))}</{key}>\n"
    s += "</ComicInfo>\n"
    return s


def create_cbz(
    src_dir: Union[Path, str], dest_file: Union[Path, str], comic_info: dict[str, Any]
):
    """
    ComicInfo.xml is generated based on comic_info dict:

    comic_info = {
        'Series': 'Manga of the Apocalypse',
        'Number': 1,
        'Title': 'The End of the World',
    }
    """
    # generate ComicInfo.xml in source directory
    with Path(src_dir, "ComicInfo.xml").open("w") as f:
        f.write(generate_comic_info(comic_info))
    # zip everything in source directory
    temp_file = Path(src_dir, "temp.cbz")
    files = [x for x in Path(src_dir).iterdir() if x.is_file()]
    with ZipFile(temp_file, mode="w", compression=ZIP_STORED) as zf:
        for file in files:
            zf.write(file, file.name)
    try:
        temp_file.replace(dest_file)
    except OSError as e:
        if e.errno == errno.EXDEV:
            shutil.move(temp_file, dest_file)
        else:
            raise
