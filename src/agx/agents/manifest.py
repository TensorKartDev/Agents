"""Agent manifest validation utilities."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Tuple


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_io_contract(name: str, value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        # Allow JSON schema-like dicts.
        return []
    if not isinstance(value, list):
        return [f"'{name}' must be a list of field specs or a schema dict"]
    errors: List[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"'{name}[{idx}]' must be a mapping")
            continue
        field_name = item.get("name")
        field_type = item.get("type")
        if not isinstance(field_name, str) or not field_name:
            errors.append(f"'{name}[{idx}].name' must be a non-empty string")
        if not isinstance(field_type, str) or not field_type:
            errors.append(f"'{name}[{idx}].type' must be a non-empty string")
        if "required" in item and not isinstance(item["required"], bool):
            errors.append(f"'{name}[{idx}].required' must be a boolean")
        if "description" in item and item["description"] is not None and not isinstance(item["description"], str):
            errors.append(f"'{name}[{idx}].description' must be a string")
    return errors


def validate_manifest(manifest: Mapping[str, Any]) -> List[str]:
    """Return a list of user-friendly validation errors."""
    errors: List[str] = []

    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Missing required field 'name' (string).")

    description = manifest.get("description")
    if description is not None and not isinstance(description, str):
        errors.append("Field 'description' must be a string.")

    icon = manifest.get("icon")
    if icon is not None and not isinstance(icon, str):
        errors.append("Field 'icon' must be a string.")

    config_path = manifest.get("config_path") or manifest.get("config")
    if config_path is not None and not isinstance(config_path, str):
        errors.append("Field 'config_path' must be a string when provided.")

    inputs = manifest.get("inputs")
    errors.extend(_validate_io_contract("inputs", inputs))

    outputs = manifest.get("outputs")
    errors.extend(_validate_io_contract("outputs", outputs))

    capabilities = manifest.get("capabilities")
    permissions = manifest.get("permissions")
    if capabilities is not None and not _is_str_list(capabilities):
        errors.append("Field 'capabilities' must be a list of strings.")
    if permissions is not None and not _is_str_list(permissions):
        errors.append("Field 'permissions' must be a list of strings.")

    version = manifest.get("version")
    if version is not None and not isinstance(version, str):
        errors.append("Field 'version' must be a string.")

    compatibility = manifest.get("compatibility")
    if compatibility is not None and not isinstance(compatibility, dict):
        errors.append("Field 'compatibility' must be a mapping.")
    elif isinstance(compatibility, dict):
        for key, value in compatibility.items():
            if value is not None and not isinstance(value, str):
                errors.append(f"compatibility.{key} must be a string.")

    pricing = manifest.get("pricing")
    if pricing is not None and not isinstance(pricing, dict):
        errors.append("Field 'pricing' must be a mapping.")
    elif isinstance(pricing, dict):
        if "model" in pricing and pricing["model"] is not None and not isinstance(pricing["model"], str):
            errors.append("pricing.model must be a string.")
        if "currency" in pricing and pricing["currency"] is not None and not isinstance(pricing["currency"], str):
            errors.append("pricing.currency must be a string.")
        if "amount" in pricing and pricing["amount"] is not None and not isinstance(
            pricing["amount"], (int, float)
        ):
            errors.append("pricing.amount must be a number.")
        if "unit" in pricing and pricing["unit"] is not None and not isinstance(pricing["unit"], str):
            errors.append("pricing.unit must be a string.")

    return errors


def normalize_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy of the manifest with backward-compatible keys normalized."""
    data = dict(manifest)
    if "capabilities" not in data and "permissions" in data:
        data["capabilities"] = data.get("permissions")
    return data
