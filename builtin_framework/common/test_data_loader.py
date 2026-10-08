from datetime import datetime
from pathlib import Path
import re

import yaml


def load_test_data(module_name, test_data_path):
    path = Path(test_data_path)
    with open(path, "r", encoding="utf-8") as file_obj:
        raw_data = yaml.safe_load(file_obj) or {}

    parameters = raw_data.get("parameters", {}) or {}
    resolved_parameters = {}
    timestamp = datetime.now().strftime("%H%M%S")
    safe_module_name = _sanitize_module_name(module_name)

    for key, value in parameters.items():
        resolved_parameters[key] = _resolve_parameter_value(
            module_name=safe_module_name,
            timestamp=timestamp,
            value=value,
        )

    return {
        "description": raw_data.get("description", f"Test data for {module_name}"),
        "parameters": resolved_parameters,
    }


def _resolve_parameter_value(module_name, timestamp, value):
    if isinstance(value, dict):
        mode = value.get("mode", "literal")
        raw_value = value.get("value")
        if mode == "module_time_hms":
            base_value = module_name if raw_value in (None, "") else str(raw_value)
            return f"{base_value}{timestamp}"
        if mode == "template":
            template = str(raw_value or "")
            return (
                template.replace("{module}", module_name)
                .replace("{time_hms}", timestamp)
            )
        return raw_value

    return value


def _sanitize_module_name(module_name):
    cleaned = re.sub(r"\s+", "", str(module_name))
    return cleaned or "test"
