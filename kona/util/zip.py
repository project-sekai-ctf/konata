import zipfile
from pathlib import Path


# cant go lower than 1980-01-01T00:00:00Z
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _zip_info(arcname: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=arcname.replace('\\', '/'), date_time=_FIXED_DATE_TIME)
    info.external_attr = 0o777 << 16
    return info


def make_zip(output_path: Path, source_files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(str(output_path.absolute()), 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for fs_path, arcname in sorted(source_files, key=lambda x: x[1]):
            zf.writestr(_zip_info(arcname), fs_path.read_bytes())
