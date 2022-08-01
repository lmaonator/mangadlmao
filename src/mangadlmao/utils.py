from pathlib import Path
from typing import Union


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
