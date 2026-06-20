"""
``env`` backend — resolve a secret from a named environment variable.

Wraps the existing ``pfactory_yml.secrets.resolve_env_var`` so behaviour matches
how ``.pfactory.yml`` auth already resolves env-var *names*. Data never leaves
the process, so the egress class is LOCAL.
"""

from __future__ import annotations

from pfactory_secrets import (
    EgressClass,
    SecretNotFoundError,
    SecretRef,
    SecretsBackend,
    SecretValue,
)


class EnvBackend(SecretsBackend):
    name = "env"

    def available(self) -> bool:
        return True

    def egress_class(self) -> EgressClass:
        return EgressClass.LOCAL

    def resolve(self, ref: SecretRef) -> SecretValue:
        from pfactory_yml.secrets import MissingSecretError, resolve_env_var

        var = ref.locator
        try:
            value = resolve_env_var(var)
        except MissingSecretError as exc:
            raise SecretNotFoundError(f"Environment variable {var!r} is not set") from exc
        return SecretValue(value=value, backend=self.name, ref=ref.raw, source=f"env:{var}")


__all__ = ["EnvBackend"]
