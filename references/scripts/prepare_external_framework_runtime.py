from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import yaml


ESSENTIAL_DIRS = [
    "common",
    "record_raw",
    "test_data",
    "tests",
]

ESSENTIAL_FILES = [
    "conftest.py",
    "pytest.ini",
    "requirements.txt",
]


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def dump_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def build_runtime_config(binding: dict) -> dict:
    return (binding.get("framework_binding", {}) or {}).get("runtime_config", {}) or {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare readonly mirror runtime for external framework")
    parser.add_argument("--binding", required=True, help="Path to framework binding yaml")
    args = parser.parse_args()

    binding_path = Path(args.binding).resolve()
    binding = load_yaml(binding_path)
    root = binding.get("framework_binding", {}) or {}

    framework_root_value = (
        root.get("framework_root")
        or root.get("external_framework_root")
    )
    if not framework_root_value:
        raise KeyError("framework_root is required in framework_binding")

    external_root = Path(str(framework_root_value)).resolve()
    runtime_workspace = Path(str(root["runtime_workspace"])).resolve()

    runtime_workspace.mkdir(parents=True, exist_ok=True)

    for name in ESSENTIAL_DIRS:
        copy_tree(external_root / name, runtime_workspace / name)

    for name in ESSENTIAL_FILES:
        copy_file(external_root / name, runtime_workspace / name)

    (runtime_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    (runtime_workspace / "allure-results").mkdir(parents=True, exist_ok=True)

    runtime_config = build_runtime_config(binding)
    dump_yaml(runtime_workspace / "config.yaml", runtime_config)

    report = {
        "binding": str(binding_path),
        "external_root": str(external_root),
        "runtime_workspace": str(runtime_workspace),
        "copied_dirs": ESSENTIAL_DIRS,
        "copied_files": ESSENTIAL_FILES,
        "config_path": str(runtime_workspace / "config.yaml"),
    }
    dump_yaml(runtime_workspace / "prepare_report.yaml", report)
    print(f"Prepared runtime workspace: {runtime_workspace}")


if __name__ == "__main__":
    main()
