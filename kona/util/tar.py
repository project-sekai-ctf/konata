import gzip
import tarfile
from io import BytesIO
from pathlib import Path


def _norm_ti(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.uid = 0
    ti.gid = 0
    ti.uname = 'kona'
    ti.gname = 'kona'
    ti.mtime = 0
    ti.pax_headers = {}
    ti.mode = 0o777
    return ti


def _open_deterministic_gzip(output_path: Path) -> gzip.GzipFile:
    return gzip.GzipFile(filename='', mode='wb', mtime=0, fileobj=output_path.open('wb'))


def make_tar_gz(output_path: Path, source_files: list[Path]) -> None:
    with (
        _open_deterministic_gzip(output_path) as gz,
        tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tar,
    ):
        for source_file in source_files:
            tar.add(source_file, arcname=source_file.name, filter=_norm_ti)


def make_tar_gz_from(output_path: Path, source_files: list[tuple[Path, str]]) -> None:
    with (
        _open_deterministic_gzip(output_path) as gz,
        tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tar,
    ):
        for fs_path, arcname in sorted(source_files, key=lambda x: x[1]):
            data = fs_path.read_bytes()
            ti = tarfile.TarInfo(name=arcname)
            ti.size = len(data)
            ti = _norm_ti(ti)
            tar.addfile(ti, BytesIO(data))
