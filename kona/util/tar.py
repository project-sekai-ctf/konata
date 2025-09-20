import gzip
import tarfile
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


def make_tar_gz(output_path: Path, source_files: list[Path]) -> None:
    with (
        gzip.open(str(output_path.absolute()), 'wb') as gz,
        tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tar,
    ):
        for source_file in source_files:
            tar.add(source_file, arcname=source_file.name, filter=_norm_ti)

    with output_path.open('r+b') as f:
        f.seek(4, 0)
        f.write(b'\x00' * 4)  # zero out the mtime in the gzip header
