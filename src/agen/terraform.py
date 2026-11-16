"""Export declared resources to Terraform JSON (``main.tf.json``) for Azure."""

from __future__ import annotations

from typing import Any, Iterable

from .errors import AgenRuntimeError, AgenTypeError
from .values import Resource, type_name

AZURE_TYPES = {
    "group": "azurerm_resource_group",
    "storage": "azurerm_storage_account",
    "container": "azurerm_storage_container",
    "queue": "azurerm_storage_queue",
    "keyvault": "azurerm_key_vault",
    "plan": "azurerm_service_plan",
    "webapp": "azurerm_linux_web_app",
    "function": "azurerm_linux_function_app",
}


def terraform_type(kind: str) -> str | None:
    """Short kinds map to azurerm types; kinds like ``azurerm_cosmosdb_account`` pass through."""
    if kind in AZURE_TYPES:
        return AZURE_TYPES[kind]
    return kind if "_" in kind else None


def _render(value: Any, types: dict[str, str]) -> Any:
    if isinstance(value, Resource):
        tf_type = types.get(value.name)
        if tf_type is None:
            raise AgenRuntimeError(f"cannot reference '{value.name}': it has no Terraform mapping",
                                   retryable=False)
        return f"${{{tf_type}.{value.name}.name}}"
    if isinstance(value, list):
        return [_render(v, types) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, types) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise AgenTypeError(f"cannot export a {type_name(value)} to Terraform")


def to_terraform(resources: Iterable[Resource]) -> tuple[dict[str, Any], list[str]]:
    types: dict[str, str] = {}
    blocks: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for res in resources:
        tf_type = terraform_type(res.kind)
        if tf_type is None:
            warnings.append(f"skipped '{res.name}': no Terraform mapping for kind '{res.kind}'")
            continue
        types[res.name] = tf_type
        attrs: dict[str, Any] = {}
        for key, value in res.config.items():
            if key == "depends_on":
                deps = value if isinstance(value, list) else [value]
                attrs["depends_on"] = [f"{types[d.name]}.{d.name}" for d in deps
                                       if isinstance(d, Resource) and d.name in types]
            else:
                attrs[key] = _render(value, types)
        attrs.setdefault("name", res.name)
        blocks.setdefault(tf_type, {})[res.name] = attrs

    document = {
        "terraform": {"required_providers": {"azurerm": {"source": "hashicorp/azurerm", "version": "~> 4.0"}}},
        "provider": {"azurerm": {"features": {}}},
        "resource": blocks,
    }
    return document, warnings
