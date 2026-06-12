import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import AliasChoices, AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.alias_generators import to_camel


@dataclass
class KonaGlobalState:
    root_path: Path


# TODO(es3n1n): this is very sketchy
kona_global_state = KonaGlobalState(root_path=Path.cwd())


class KonaModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class KonaEndpointType(StrEnum):
    HTTP = 'http'
    HTTPS = 'https'
    SOCAT = 'socat'
    NC = 'nc'
    NCAT_SSL = 'ncat-ssl'


class AttachmentAdditionalFile(KonaModel):
    path: str
    str_content: str | None = Field(default=None, validation_alias=AliasChoices('str', 'strContent', 'str_content'))
    base64_content: str | None = Field(
        default=None, validation_alias=AliasChoices('base64', 'base64Content', 'base64_content')
    )

    @model_validator(mode='after')
    def exactly_one_content(self) -> 'AttachmentAdditionalFile':
        has_str = self.str_content is not None
        has_b64 = self.base64_content is not None
        if has_str == has_b64:
            msg = 'exactly one of str or base64 must be provided'
            raise ValueError(msg)
        return self


class AttachmentConfig(KonaModel):
    files: list[str] = []
    exclude: list[str] = []
    additional: list[AttachmentAdditionalFile] = []
    pre_compressed: list[str] = []
    archive_name: str | None = None


class FlagValue(KonaModel):
    str_content: str | None = Field(default=None, validation_alias=AliasChoices('str', 'strContent', 'str_content'))
    file: str | None = None

    @model_validator(mode='after')
    def exactly_one_source(self) -> 'FlagValue':
        has_str = self.str_content is not None
        has_file = self.file is not None
        if has_str == has_file:
            msg = 'exactly one of str or file must be provided'
            raise ValueError(msg)
        return self

    def resolve(self, challenge_dir: Path) -> str:
        if self.str_content is not None:
            return self.str_content
        if self.file is not None:
            return (challenge_dir / self.file).read_text().strip()
        raise RuntimeError


class AdminBotConfig(KonaModel):
    code: str | None = None
    file: str | None = None

    @model_validator(mode='after')
    def exactly_one_source(self) -> 'AdminBotConfig':
        has_code = self.code is not None
        has_file = self.file is not None
        if has_code == has_file:
            msg = 'exactly one of code or file must be provided'
            raise ValueError(msg)
        return self

    def resolve(self, challenge_dir: Path) -> str:
        if self.code is not None:
            return self.code
        if self.file is not None:
            return (challenge_dir / self.file).read_text()
        raise RuntimeError


class KonaChallengeItem(KonaModel):
    class CTFD(KonaModel):
        class Hint(KonaModel):
            hint: str
            cost: int = 0
            title: str | None = None

        type: str = 'dynamic'
        topics: list[str] = []
        hints: list[Hint] = []
        connection_info: str = ''

    class Scoring(KonaModel):
        class CTFD(KonaModel):
            decay_function: str = 'logarithmic'
            decay: int = 60
            max_attempts: int = 0

        class RCTF(KonaModel):
            eligible_for_tiebreaks: bool = True

        ctfd: CTFD = CTFD()
        rctf: RCTF = RCTF()
        initial_value: int = 500
        minimum_value: int = 100

    class Flags(KonaModel):
        class CTFDFlag(KonaModel):
            type: str = 'static'
            flag: str | FlagValue

        rctf: str | FlagValue = ''
        ctfd: list[CTFDFlag] = []

    class InstancerConfig(KonaModel):
        class ExposeKind(StrEnum):
            TCP = 'tcp'
            TCP_SSL = 'tcp-ssl'
            HTTP = 'http'
            HTTPS = 'https'

        class Expose(KonaModel):
            kind: 'KonaChallengeItem.InstancerConfig.ExposeKind'
            host_prefix: str
            container_name: str
            container_port: int
            should_display: bool = True
            title: str | None = None

        challenge_integration_id: str
        config: Any = {}
        expose: list[Expose] = []
        timeout_milliseconds: int | None = None
        extendable: bool = True

    class Endpoint(KonaModel):
        name: str | None = None
        type: KonaEndpointType
        endpoint: str
        port: int | None = None

        @property
        def name_prefix(self) -> str:
            return f'{self.name}: ' if self.name else ''

        @property
        def http_port_if_needed(self) -> str:
            return f':{self.port}' if self.port else ''

        @property
        def http_endpoint(self) -> str:
            return f'{self.type.value}://{self.endpoint}{self.http_port_if_needed}'

    category: str
    name: str
    author: str
    override_id: str | None = None

    description: str = ''
    tags: list[str] = []

    attachments: list[str] | AttachmentConfig = []

    scoring: Scoring = Scoring()
    flags: Flags = Flags()
    endpoints: list[Endpoint] = []

    hidden: bool = False
    sort_weight: int | None = None

    ctfd: CTFD = CTFD()
    instancer_config: InstancerConfig | None = None
    admin_bot: AdminBotConfig | None = None

    @property
    def challenge_id(self) -> str:
        if self.override_id is not None:
            return self.override_id
        return f'{self.category}_{self.name}'

    @field_validator('description')
    @classmethod
    def strip_description(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode='after')
    def flag_is_set(self) -> 'KonaChallengeItem':
        if self.flags.rctf or self.flags.ctfd:
            return self
        logger.warning(f'No flags set for challenge {self.challenge_id}')
        return self

    @model_validator(mode='after')
    def warn_attachments(self) -> 'KonaChallengeItem':
        has_attachments = (
            bool(self.attachments)
            if isinstance(self.attachments, list)
            else bool(self.attachments.files or self.attachments.pre_compressed)
        )
        if not has_attachments:
            logger.warning(f'No attachments set for challenge {self.challenge_id}')
        return self

    def resolve_flags(self, challenge_dir: Path) -> None:
        if isinstance(self.flags.rctf, FlagValue):
            self.flags.rctf = self.flags.rctf.resolve(challenge_dir)
        for flag in self.flags.ctfd:
            if isinstance(flag.flag, FlagValue):
                flag.flag = flag.flag.resolve(challenge_dir)

    def resolve_admin_bot(self, challenge_dir: Path) -> None:
        if self.admin_bot is not None:
            self.admin_bot.code = self.admin_bot.resolve(challenge_dir)
            self.admin_bot.file = None


class KonaRolloutRestartConfig(KonaModel):
    annotation_path: str | None = None
    image: bool = True


class KonaChallengeConfig(KonaModel):
    class DiscoveryConfig(KonaModel):
        skip: bool = False

    class ChallengeDeploymentConfig(KonaModel):
        class DockerImage(KonaModel):
            class Export(KonaModel):
                stage: str
                src: str = '/out'
                dst: str = ''

            build_context: str = Field(validation_alias=AliasChoices('build_context', 'buildContext', 'path'))
            dockerfile: str | None = None
            name: str
            tag: str = 'latest'
            registry_name: str | None = None
            build_args: dict[str, str] = {}
            platform: str | None = None
            no_cache: bool = False
            exports: list[Export] = []

        class KonaKubernetesManifest(KonaModel):
            paths: list[str]
            cluster_name: str | None = None
            rollout_restart: KonaRolloutRestartConfig = KonaRolloutRestartConfig()

        class KonaKubernetesInlineManifest(KonaModel):
            documents: list[dict[str, Any]]
            cluster_name: str | None = None
            rollout_restart: KonaRolloutRestartConfig = KonaRolloutRestartConfig()

        images: list[DockerImage] = []
        # TODO(es3n1n): rename kubernetes_inline_manifests to just kubernetes_manifests
        kubernetes_manifests: list[KonaKubernetesManifest] = []
        kubernetes_inline_manifests: list[KonaKubernetesInlineManifest] = []

    discovery: DiscoveryConfig = DiscoveryConfig()
    challenges: list[KonaChallengeItem] = []
    deployment: ChallengeDeploymentConfig = ChallengeDeploymentConfig()


class KonaSecret(KonaModel):
    file_path: str | None = None
    value: SecretStr | None = None
    env: str | None = None

    @model_validator(mode='after')
    def exactly_one_of(self) -> 'KonaSecret':
        provided = [f for f in ('file_path', 'value', 'env') if getattr(self, f) is not None]
        if len(provided) != 1:
            msg = 'exactly one of file_path, value, env must be provided'
            raise ValueError(msg)
        return self

    @property
    def loaded(self) -> str:
        if self.value is not None:
            return self.value.get_secret_value()

        if self.file_path is not None:
            file_path = Path(self.file_path)
            if self.file_path.startswith('.'):
                # TODO(es3n1n): move to an util
                file_path = (kona_global_state.root_path / self.file_path).resolve().absolute()

            if not file_path.exists():
                msg = f'{file_path} does not exist'
                raise FileNotFoundError(msg)

            self.value = SecretStr(file_path.read_text())
            return self.value.get_secret_value()

        if self.env is not None:
            value = os.getenv(self.env)
            if value is None:
                msg = f'Environment variable {self.env} is not set'
                raise ValueError(msg)
            self.value = SecretStr(value)
            return self.value.get_secret_value()

        raise RuntimeError


class KonaSecretOrValue(KonaModel):
    secret: str | None = None
    value: SecretStr | None = None

    @model_validator(mode='after')
    def exactly_one_of(self) -> 'KonaSecretOrValue':
        provided = [f for f in ('secret', 'value') if getattr(self, f) is not None]
        if len(provided) != 1:
            msg = 'exactly one of secret, value must be provided'
            raise ValueError(msg)
        return self

    def load(self, global_config: 'KonaGlobalConfig') -> str:
        if self.secret is not None:
            return global_config.secrets[self.secret].loaded
        if self.value is not None:
            return self.value.get_secret_value()
        raise RuntimeError


class KonaRCTFCredentials(KonaModel):
    base_url: AnyHttpUrl
    team_token: KonaSecretOrValue
    extra_headers: dict[str, str] = {}


class KonaCTFDCredentials(KonaModel):
    base_url: AnyHttpUrl
    admin_token: KonaSecretOrValue
    extra_headers: dict[str, str] = {}


class KonaDiscoveryConfig(KonaModel):
    challenge_folder_depth: int = 3
    attachment_analysis_depth: int = 50
    klodd_domain: str | None = None
    klodd_endpoint_name: str | None = None


class KonaTemplatesConfig(KonaModel):
    class EndpointsText(KonaModel):
        ctfd: str = """{% for endpoint in challenge.endpoints -%}
{% if endpoint.type == models.KonaEndpointType.SOCAT %}
{{ endpoint.name_prefix }}`socat -,raw,echo=0 tcp:{{ endpoint.endpoint }}:{{ endpoint.port or 1337 }}`
{% elif endpoint.type == models.KonaEndpointType.NC %}
{{ endpoint.name_prefix }}`nc {{ endpoint.endpoint }} {{ endpoint.port or 1337 }}`
{% elif endpoint.type == models.KonaEndpointType.NCAT_SSL %}
{{ endpoint.name_prefix }}`ncat --ssl {{ endpoint.endpoint }} {{ endpoint.port or 1337 }}`
{% elif endpoint.type in (models.KonaEndpointType.HTTP, models.KonaEndpointType.HTTPS) %}
{% if endpoint.name -%}
[{{ endpoint.name }}]({{ endpoint.http_endpoint }})
{% else -%}
[{{ endpoint.http_endpoint }}]({{ endpoint.http_endpoint }})
{% endif -%}
{% else -%}
unknown endpoint type {{ endpoint.type }}
{% endif -%}
{% endfor -%}"""
        rctf: str = """{% for endpoint in challenge.endpoints %}
> [!CONNECTION]
{% if endpoint.type == models.KonaEndpointType.SOCAT -%}
> {{ endpoint.name_prefix }}socat -,raw,echo=0 tcp:{{ endpoint.endpoint }}:{{ endpoint.port or 1337 }}
{%- elif endpoint.type == models.KonaEndpointType.NC -%}
> {{ endpoint.name_prefix }}nc {{ endpoint.endpoint }} {{ endpoint.port or 1337 }}
{%- elif endpoint.type == models.KonaEndpointType.NCAT_SSL -%}
> {{ endpoint.name_prefix }}ncat --ssl {{ endpoint.endpoint }} {{ endpoint.port or 1337 }}
{%- elif endpoint.type in (models.KonaEndpointType.HTTP, models.KonaEndpointType.HTTPS) -%}
> {{ endpoint.http_endpoint }}
{%- else -%}
> unknown endpoint type {{ endpoint.type }}
{%- endif %}
{% endfor -%}"""

        @field_validator('ctfd', 'rctf')
        @classmethod
        def strip_values(cls, v: str) -> str:
            return v.strip()

    # TODO(es3n1n): inlining text here is ugly, consider loading from files
    challenge_description: str = (
        '{{ challenge.description }}\n\n{{ endpoints_rendered.strip() }}\n\n**Author**: {{ challenge.author }}'
    )
    endpoints_text: EndpointsText = EndpointsText()
    ctfd_attribution: str = '**Author**: {{ challenge.author }}'

    @field_validator('challenge_description', 'ctfd_attribution')
    @classmethod
    def strip_values(cls, v: str) -> str:
        return v.strip()


class KonaGcloudClusterConfig(KonaModel):
    cluster_name: str
    project: str
    zone: str


class KonaKindClusterConfig(KonaModel):
    cluster_name: str = 'kind'


class KonaKubernetesClusterConfig(KonaModel):
    incluster: bool = False
    # KUBECONFIG or ~/.kube/config
    use_default: bool = True
    kubeconfig: KonaSecretOrValue | None = None
    gcloud: KonaGcloudClusterConfig | None = None
    kind: KonaKindClusterConfig | None = None
    alias_to: str | list[str] | None = None


class AttachmentFormat(StrEnum):
    TAR_GZ = 'tar_gz'
    ZIP = 'zip'


class KonaGlobalConfig(KonaModel):
    discovery: KonaDiscoveryConfig = KonaDiscoveryConfig()
    secrets: dict[str, KonaSecret] = {}
    rctf: KonaRCTFCredentials | None = None
    ctfd: KonaCTFDCredentials | None = None
    templates: KonaTemplatesConfig = KonaTemplatesConfig()
    registries: dict[str, str] = {}
    clusters: dict[str, KonaKubernetesClusterConfig] = {}
    domains: dict[str, str] = {}
    attachment_format: AttachmentFormat = AttachmentFormat.TAR_GZ
