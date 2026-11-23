"""Load a domain pack folder: its YAML specification and its connector module."""

import importlib.util
from pathlib import Path
from typing import Any, cast

import yaml

from verified_rag.packs.interface import PACK_ID, ConnectorFactory, Pack, PackSpec


class PackError(RuntimeError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PackError(f"missing pack file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PackError(f"{path} must contain a mapping")
    return cast(dict[str, Any], data)


def load_spec(pack_id: str, packs_dir: Path) -> PackSpec:
    if not PACK_ID.match(pack_id):
        raise PackError(f"invalid pack id {pack_id!r}")
    root = packs_dir / pack_id
    data = _read_yaml(root / "pack.yaml")
    data["concepts"] = _read_yaml(root / "concepts.yaml").get("concepts", [])
    data["units"] = _read_yaml(root / "units.yaml").get("units", {})
    spec = PackSpec.model_validate(data)
    if spec.id != pack_id:
        raise PackError(f"{root / 'pack.yaml'} declares id {spec.id!r}, expected {pack_id!r}")
    return spec


def load_pack(pack_id: str, packs_dir: Path) -> Pack:
    spec = load_spec(pack_id, packs_dir)
    root = packs_dir / pack_id
    module_path = root / "connector.py"
    module_spec = importlib.util.spec_from_file_location(
        f"verified_rag_pack_{pack_id}_connector", module_path
    )
    if module_spec is None or module_spec.loader is None:
        raise PackError(f"can't load {module_path}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    factory = getattr(module, "create_connector", None)
    if not callable(factory):
        raise PackError(f"{module_path} must define create_connector(ctx)")
    prompt_path = root / "prompt.md"
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    return Pack(
        spec=spec, root=root, create_connector=cast(ConnectorFactory, factory), prompt=prompt
    )
