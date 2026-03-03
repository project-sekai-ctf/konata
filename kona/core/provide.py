import base64
from fnmatch import fnmatch
from pathlib import Path

from kona.schema.models import AttachmentConfig, AttachmentFormat
from kona.util.tar import make_tar_gz_from
from kona.util.zip import make_zip


def _normalize_config(attachments: list[str] | AttachmentConfig) -> AttachmentConfig:
    if isinstance(attachments, list):
        return AttachmentConfig(files=attachments)
    return attachments


def _collect_paths(challenge_dir: Path, patterns: list[str]) -> list[Path]:
    result: list[Path] = []
    for pattern in patterns:
        path = challenge_dir / pattern
        if path.is_dir():
            result.extend(p for p in path.rglob('*') if p.is_file())
        elif any(c in pattern for c in ('*', '?', '[')):
            result.extend(p for p in challenge_dir.glob(pattern) if p.is_file())
        elif path.is_file():
            result.append(path)
    return result


def _is_excluded(rel_path: str, exclude_patterns: list[str]) -> bool:
    return any(fnmatch(rel_path, pat) for pat in exclude_patterns)


def resolve_source_paths(challenge_dir: Path, attachments: list[str] | AttachmentConfig) -> list[Path]:
    cfg = _normalize_config(attachments)
    paths = _collect_paths(challenge_dir, cfg.files)

    if cfg.exclude:
        paths = [p for p in paths if not _is_excluded(str(p.relative_to(challenge_dir)), cfg.exclude)]

    return paths


def resolve_attachments(
    challenge_dir: Path,
    tmp_dir: Path,
    attachments: list[str] | AttachmentConfig,
    fmt: AttachmentFormat,
    challenge_id: str,
) -> list[Path]:
    cfg = _normalize_config(attachments)
    result: list[Path] = []

    collected = _collect_paths(challenge_dir, cfg.files)

    if cfg.exclude:
        collected = [p for p in collected if not _is_excluded(str(p.relative_to(challenge_dir)), cfg.exclude)]

    entries: list[tuple[Path, str]] = [(p, str(p.relative_to(challenge_dir))) for p in collected]
    for additional in cfg.additional:
        dest = tmp_dir / additional.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if additional.str_content is not None:
            dest.write_text(additional.str_content)
        elif additional.base64_content is not None:
            dest.write_bytes(base64.b64decode(additional.base64_content))
        entries.append((dest, additional.path))

    if entries:
        archive_name = f'{challenge_id}.zip' if fmt == AttachmentFormat.ZIP else f'{challenge_id}.tar.gz'
        archive_path = tmp_dir / archive_name
        if fmt == AttachmentFormat.ZIP:
            make_zip(archive_path, entries)
        else:
            make_tar_gz_from(archive_path, entries)
        result.append(archive_path)

    for pre in cfg.pre_compressed:
        pre_path = challenge_dir / pre
        if pre_path.is_file():
            result.append(pre_path)

    return result
