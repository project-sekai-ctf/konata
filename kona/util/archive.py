from pathlib import Path

from kona.schema.models import AttachmentFormat
from kona.util.sevenzip import make_7z
from kona.util.tar import make_tar_gz
from kona.util.zip import make_zip


def archive_format_for_password(fmt: AttachmentFormat, password: str | None) -> AttachmentFormat:
    if password:
        return AttachmentFormat.SEVEN_Z
    return fmt


def make_archive(
    output_path: Path,
    source_files: list[tuple[Path, str]],
    fmt: AttachmentFormat,
    password: str | None = None,
) -> AttachmentFormat:
    fmt = archive_format_for_password(fmt, password)

    if fmt == AttachmentFormat.ZIP:
        make_zip(output_path, source_files)
    elif fmt == AttachmentFormat.SEVEN_Z:
        make_7z(output_path, source_files, password)
    else:
        make_tar_gz(output_path, source_files)

    return fmt
