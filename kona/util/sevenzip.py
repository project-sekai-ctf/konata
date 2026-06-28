from pathlib import Path
from typing import IO, Any

import py7zr
from py7zr.helpers import ArchiveTimestamp
from py7zr.py7zr import FileInfoDict, MemberType


# cant go lower than 1980-01-01T00:00:00Z
_FIXED_TIMESTAMP = ArchiveTimestamp.from_datetime(315532800)


class _NormalizedSevenZipFile(py7zr.SevenZipFile):
    @staticmethod
    def _make_file_info_from_name(bio: IO[Any], size: int, arcname: str) -> FileInfoDict:
        return FileInfoDict(
            origin=None,
            data=bio,
            filename=Path(arcname).as_posix(),
            uncompressed=size,
            emptystream=False,
            attributes=MemberType.FILE.attributes(),
            creationtime=_FIXED_TIMESTAMP,
            lastwritetime=_FIXED_TIMESTAMP,
            lastaccesstime=_FIXED_TIMESTAMP,
        )


def make_7z(output_path: Path, source_files: list[tuple[Path, str]], password: str | None = None) -> None:
    with _NormalizedSevenZipFile(
        output_path,
        'w',
        password=password,
        header_encryption=password is not None,
    ) as archive:
        for fs_path, arcname in sorted(source_files, key=lambda x: x[1]):
            archive.writestr(fs_path.read_bytes(), arcname.replace('\\', '/'))
