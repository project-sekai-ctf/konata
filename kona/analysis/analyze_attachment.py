from collections import defaultdict
from enum import StrEnum
from pathlib import Path

from loguru import logger

from kona.schema.models import KonaGlobalConfig, kona_global_state

from .passes import AnalysisContext, analysis_pass


class CountedDifferencesType(StrEnum):
    LINES = 'lines'
    BYTES = 'bytes'


def looks_like_text(file_data: bytes) -> bool:
    return any(not (ord(' ') <= x <= ord('~')) for x in file_data[:100])


def generic_diff(left: list[str] | bytes | str, right: list[str] | bytes | str) -> int:
    result = 0

    for i in range(max(len(left), len(right))):
        left_line = left[i] if i < len(left) else None
        right_line = right[i] if i < len(right) else None

        if left_line == right_line:
            continue

        result += 1

    return result


def diff(left: bytes, right: bytes) -> tuple[CountedDifferencesType, int]:
    if looks_like_text(left) and looks_like_text(right):
        return CountedDifferencesType.LINES, generic_diff(left.decode().splitlines(), right.decode().splitlines())
    return CountedDifferencesType.BYTES, generic_diff(left, right)


def build_files_dict(
    global_config: KonaGlobalConfig, result: defaultdict[str, list[Path]], *items: Path, depth: int = 0
) -> defaultdict[str, list[Path]]:
    if depth > global_config.discovery.attachment_analysis_depth:
        return result

    for item in items:
        if item.is_dir():
            build_files_dict(global_config, result, *list(item.iterdir()), depth=depth + 1)
            continue

        result[item.name].append(item.resolve().absolute())

    return result


@analysis_pass
async def analyze_attachment(context: AnalysisContext) -> None:
    for group in context.sync_result.groups:
        attachments_dict = build_files_dict(
            context.global_config,
            defaultdict(list),
            *(attachment for chal in group.challenges for attachment in chal.attachments),
        )

        for image in group.deployment_result.built_docker_images:
            # Let's see what files are built for the docker image and if they are in attachments
            image_dict = build_files_dict(context.global_config, defaultdict(list), image.path)

            for file, image_paths in image_dict.items():
                attachment_files = attachments_dict.get(file)
                if not attachment_files:
                    continue

                for attachment_file, image_file in zip(attachment_files, image_paths, strict=False):
                    diff_type, diff_result = diff(image_file.read_bytes(), attachment_file.read_bytes())
                    if not diff_result:
                        # same content
                        continue

                    img_rel = image_file.relative_to(kona_global_state.root_path)
                    attachment_rel = attachment_file.relative_to(kona_global_state.root_path)
                    logger.warning(
                        f'Difference between {img_rel} and {attachment_rel} is {diff_result} {diff_type.name}'
                    )
