from typing import Any

from jinja2.sandbox import SandboxedEnvironment

from kona.schema import models


def render_template(template: str, **kwargs: Any) -> str:  # noqa: ANN401
    env = SandboxedEnvironment()
    tmpl = env.from_string(template)
    return tmpl.render(models=models, **kwargs)
