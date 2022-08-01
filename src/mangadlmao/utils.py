from pathlib import Path
from typing import Union


def sanitize_path(path: Union[str, Path]):
    # make path component windows compatible
    return str(path).lstrip(". ").replace("/", "-").replace("\\", "-") \
        .replace(":", "").replace("?", "").replace("*", "").replace("|", "") \
        .replace("<", "_").replace(">", "_").replace("\"", "'").strip()
