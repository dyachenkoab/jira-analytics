import json
import re
from dataclasses import dataclass
from pathlib import Path

MODULE_API_VERSION = 1
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ModuleDescriptor:
    module_id: str
    title: str
    qml_path: Path
    order: int
    builtin: bool


def _read_manifest(module_dir: Path) -> dict | None:
    manifest_path = module_dir / "module.json"
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _validate_manifest(module_dir: Path, data: dict) -> ModuleDescriptor | None:
    module_id = data.get("id")
    title = data.get("title")
    qml_rel = data.get("qml")
    order = data.get("order")
    api_version = data.get("apiVersion")

    if not isinstance(module_id, str) or not ID_RE.fullmatch(module_id):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(qml_rel, str) or not qml_rel.strip():
        return None
    if isinstance(order, bool) or not isinstance(order, int):
        return None
    if type(api_version) is not int or api_version != MODULE_API_VERSION:
        return None

    try:
        qml_path = (module_dir / qml_rel).resolve()
        qml_relative = qml_path.relative_to(module_dir.resolve())
    except (ValueError, OSError, RuntimeError):
        return None

    if not qml_path.is_file():
        return None

    return ModuleDescriptor(
        module_id=module_id,
        title=title.strip(),
        qml_path=qml_path,
        order=order,
        builtin=True,
    )


def _scan_modules(root: Path) -> tuple[list[ModuleDescriptor], list[str]]:
    modules: list[ModuleDescriptor] = []
    errors: list[str] = []

    if not root.is_dir():
        return modules, errors

    try:
        items = sorted(root.iterdir())
    except OSError:
        errors.append(f"cannot list directory: {root}")
        return modules, errors

    for item in items:
        if not item.is_dir():
            continue
        data = _read_manifest(item)
        if data is None:
            if (item / "module.json").is_file():
                errors.append(f"{item}/module.json: invalid manifest")
            continue
        try:
            descriptor = _validate_manifest(item, data)
        except (OSError, RuntimeError):
            errors.append(f"{item}/module.json: invalid manifest")
            continue
        if descriptor is None:
            errors.append(f"{item}/module.json: invalid manifest")
            continue
        modules.append(descriptor)

    return modules, errors


def discover_modules(
    builtin_dir: Path,
    user_dir: Path | None = None,
) -> tuple[list[ModuleDescriptor], list[str]]:
    builtin_modules, builtin_errors = _scan_modules(builtin_dir)

    if not builtin_dir.is_dir():
        return [], [f"builtin modules directory not found: {builtin_dir}"]

    seen_ids: set[str] = set()
    merged: list[ModuleDescriptor] = []
    errors: list[str] = []

    for m in builtin_modules:
        if m.module_id in seen_ids:
            continue
        seen_ids.add(m.module_id)
        merged.append(m)
    errors.extend(builtin_errors)

    if user_dir is not None and user_dir.is_dir():
        user_modules, user_errors = _scan_modules(user_dir)
        for m in user_modules:
            if m.module_id in seen_ids:
                continue
            seen_ids.add(m.module_id)
            merged.append(ModuleDescriptor(
                module_id=m.module_id,
                title=m.title,
                qml_path=m.qml_path,
                order=m.order,
                builtin=False,
            ))
        errors.extend(user_errors)

    merged.sort(key=lambda m: (m.order, m.module_id))
    return merged, errors
