from __future__ import annotations

from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile


def create_release_zip(root: Path, destination: Path) -> None:
    root = root.resolve(strict=True)
    destination = destination.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            archive.write(path, f"{root.name}/{relative}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: create_release_zip.py RELEASE_ROOT ZIP_PATH")
    create_release_zip(Path(sys.argv[1]), Path(sys.argv[2]))
