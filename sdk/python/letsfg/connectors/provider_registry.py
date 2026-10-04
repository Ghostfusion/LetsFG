"""Provider registry — empty without the credential (implementation plan P2.1).

The registry is the only thing the client is allowed to know about providers, and it
is deliberately lazy: importing it must not import an adapter, and an adapter must not
exist as a usable object when its credential is absent. That is what "optional" means
here (design §3.2) — not a flag on a module that is loaded anyway.

There is no registration hook. A provider is available or it is not, decided by the
environment, and the differences between providers are declared in their
``capabilities`` rather than negotiated at runtime (fli study §6.3).
"""

from __future__ import annotations

from importlib import import_module
from typing import Mapping, Optional

__all__ = ["REGISTRY", "available_providers", "is_available", "get_provider"]


#: name → (module path, class name, environment variable that must be set).
#: The module path is a string precisely so that nothing is imported until asked.
REGISTRY: dict[str, tuple[str, str, str]] = {
    "serpapi_google": ("letsfg.connectors.serpapi_google", "SerpApiGoogleProvider", "SERPAPI_KEY"),
}


def _configured(env_var: str, source: Mapping[str, str]) -> bool:
    return bool((source.get(env_var) or "").strip())


def available_providers(env: Optional[Mapping[str, str]] = None) -> tuple[str, ...]:
    """Names whose credential is present. Empty is a valid, expected answer."""
    import os

    source = os.environ if env is None else env
    return tuple(name for name, (_, _, env_var) in sorted(REGISTRY.items()) if _configured(env_var, source))


def is_available(name: str, env: Optional[Mapping[str, str]] = None) -> bool:
    return name in available_providers(env)


def get_provider(name: str, env: Optional[Mapping[str, str]] = None):
    """Import and construct a provider, or refuse.

    A missing credential is refused *before* the import, so an unconfigured lane
    cannot even be loaded by accident; the adapter repeats the check itself, because
    the two entry points must not disagree about what "configured" means.
    """
    if name not in REGISTRY:
        raise KeyError(f"{name} is not a registered provider. Registered: {sorted(REGISTRY)}")
    import os

    source = os.environ if env is None else env
    module_path, class_name, env_var = REGISTRY[name]
    if not _configured(env_var, source):
        from letsfg.connectors.serpapi_google import ProviderCredentialError

        raise ProviderCredentialError(
            f"provider {name!r} needs {env_var} in the environment; the registry will not "
            f"import the adapter without it (design §3.2)."
        )
    provider_class = getattr(import_module(module_path), class_name)
    return provider_class(env=source)
