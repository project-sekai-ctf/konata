import zipfile
from pathlib import Path


_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def make_zip(output_path: Path, source_files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(str(output_path.absolute()), 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for fs_path, arcname in sorted(source_files, key=lambda x: x[1]):
            info = zipfile.ZipInfo(filename=arcname, date_time=_FIXED_DATE_TIME)
            info.external_attr = 0o777 << 16
            zf.writestr(info, fs_path.read_bytes())
