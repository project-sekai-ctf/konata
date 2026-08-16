import re
from typing import Any

from jinja2.sandbox import SandboxedEnvironment

from kona.schema import models


def render_template(template: str, **kwargs: Any) -> str:  # noqa: ANN401
    env = SandboxedEnvironment()
    env.filters['re_escape'] = re.escape
    tmpl = env.from_string(template)

    context: dict[str, Any] = {'models': models}
    if models.kona_global_state.global_config is not None:
        context['config'] = models.kona_global_state.global_config
        context['kv'] = models.kona_global_state.global_config.kv

    context.update(kwargs)
    return tmpl.render(**context)


def render_template_values(obj: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    if isinstance(obj, str):
        return render_template(obj, **kwargs)
    if isinstance(obj, dict):
        return {k: render_template_values(v, **kwargs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_template_values(v, **kwargs) for v in obj]
    return obj
