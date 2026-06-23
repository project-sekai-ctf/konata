import zipfile
from pathlib import Path

import pyzipper


_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _zip_info(info_cls: type[zipfile.ZipInfo], arcname: str) -> zipfile.ZipInfo:
    info = info_cls(filename=arcname.replace('\\', '/'), date_time=_FIXED_DATE_TIME)
    info.external_attr = 0o777 << 16
    return info


def make_zip(output_path: Path, source_files: list[tuple[Path, str]], password: str | None = None) -> None:
    if password:
        _make_encrypted_zip(output_path, source_files, password)
        return

    with zipfile.ZipFile(str(output_path.absolute()), 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for fs_path, arcname in sorted(source_files, key=lambda x: x[1]):
            zf.writestr(_zip_info(zipfile.ZipInfo, arcname), fs_path.read_bytes())


def _make_encrypted_zip(output_path: Path, source_files: list[tuple[Path, str]], password: str) -> None:
    with pyzipper.AESZipFile(
        str(output_path.absolute()),
        'w',
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode())
        for fs_path, arcname in sorted(source_files, key=lambda x: x[1]):
            zf.writestr(_zip_info(pyzipper.AESZipFile.zipinfo_cls, arcname), fs_path.read_bytes())
