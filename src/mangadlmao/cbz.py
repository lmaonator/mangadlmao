from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_STORED, ZipFile


def generate_comic_info(comic_info: dict[str]):
    s = '<?xml version="1.0" encoding="UTF-8"?>\n<ComicInfo>\n'
    for key, value in comic_info.items():
        if value is not None:
            key = key[0].upper() + key[1:]
            s += f'\t<{key}>{escape(value)}</{key}>\n'
    s += '</ComicInfo>\n'
    return s


def create_cbz(src_dir: str, dest_file: str, comic_info: dict[str]):
    """
    ComicInfo.xml is generated based on comic_info dict:

    comic_info = {
        'Series': 'Tokyo Mew Mew',
        'Number': 1,
        'Title': 'Retasu Short Story',
    }
    """
    # generate ComicInfo.xml in source directory
    with Path(src_dir, 'ComicInfo.xml').open('w') as f:
        f.write(generate_comic_info(comic_info))
    # zip everything in source directory
    with ZipFile(dest_file, mode='a', compression=ZIP_STORED) as zf:
        for file in (x for x in Path(src_dir).iterdir() if x.is_file()):
            zf.write(file, file.name)
