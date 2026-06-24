import base64
import re
from fnmatch import fnmatch
from pathlib import Path

from kona.schema.models import AttachmentConfig, AttachmentFormat
from kona.util.tar import make_tar_gz_from
from kona.util.zip import make_zip


# Characters that are illegal in filenames on Windows
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str) -> str:
    return _UNSAFE_FILENAME_RE.sub('_', name)


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
            matches = [p for p in challenge_dir.glob(pattern) if p.is_file()]
            if not matches:
                msg = f'Attachment pattern "{pattern}" matched no files in {challenge_dir}'
                raise FileNotFoundError(msg)
            result.extend(matches)
        elif path.is_file():
            result.append(path)
        else:
            msg = f'Attachment "{pattern}" not found in {challenge_dir}'
            raise FileNotFoundError(msg)
    return result


def _is_excluded(rel_path: str, exclude_patterns: list[str]) -> bool:
    return any(fnmatch(rel_path, pat) for pat in exclude_patterns)


def _arcname(path: Path, challenge_dir: Path, strip_components: int) -> str:
    parts = path.relative_to(challenge_dir).parts
    stripped = parts[strip_components:] if strip_components else parts
    return '/'.join(stripped or parts[-1:])


def _materialize_additional(cfg: AttachmentConfig, tmp_dir: Path) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for additional in cfg.additional:
        dest = tmp_dir / additional.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if additional.str_content is not None:
            dest.write_text(additional.str_content)
        elif additional.base64_content is not None:
            dest.write_bytes(base64.b64decode(additional.base64_content))
        entries.append((dest, additional.path))
    return entries


def resolve_source_paths(challenge_dir: Path, attachments: list[str] | AttachmentConfig) -> list[Path]:
    cfg = _normalize_config(attachments)
    paths = _collect_paths(challenge_dir, cfg.files)

    if cfg.exclude:
        paths = [p for p in paths if not _is_excluded(p.relative_to(challenge_dir).as_posix(), cfg.exclude)]

    return paths


def resolve_attachments(
    challenge_dir: Path,
    tmp_dir: Path,
    attachments: list[str] | AttachmentConfig,
    fmt: AttachmentFormat,
    default_archive_name: str,
    extra_entries: list[tuple[Path, str]] | None = None,
    *,
    wrap_dir: bool = True,
) -> list[Path]:
    cfg = _normalize_config(attachments)
    result: list[Path] = []

    collected = _collect_paths(challenge_dir, cfg.files)

    if cfg.exclude:
        collected = [p for p in collected if not _is_excluded(p.relative_to(challenge_dir).as_posix(), cfg.exclude)]

    base = _safe_filename(cfg.archive_name or default_archive_name)

    entries: list[tuple[Path, str]] = [(p, _arcname(p, challenge_dir, cfg.strip_components)) for p in collected]
    entries.extend(_materialize_additional(cfg, tmp_dir))

    if extra_entries:
        entries.extend(extra_entries)

    if wrap_dir:
        entries = [(p, f'{base}/{arcname}') for p, arcname in entries]

    if entries:
        # tar.gz has no native encryption
        if cfg.password:
            fmt = AttachmentFormat.ZIP

        archive_name = f'{base}.zip' if fmt == AttachmentFormat.ZIP else f'{base}.tar.gz'
        archive_path = tmp_dir / archive_name
        if fmt == AttachmentFormat.ZIP:
            make_zip(archive_path, entries, cfg.password)
        else:
            make_tar_gz_from(archive_path, entries)
        result.append(archive_path)

    for pre in cfg.pre_compressed:
        pre_path = challenge_dir / pre
        if not pre_path.is_file():
            msg = f'Pre-compressed attachment "{pre}" not found in {challenge_dir}'
            raise FileNotFoundError(msg)
        result.append(pre_path)

    return result
