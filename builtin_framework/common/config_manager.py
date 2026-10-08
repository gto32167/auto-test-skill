import yaml
import os
from pathlib import Path


class ConfigManager:
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config.yaml"
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        default_config = self._get_default_config()
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
                return self._deep_merge(default_config, loaded)
        return default_config

    def _get_default_config(self):
        return {
            "browser": "chromium",
            "headless": False,
            "viewport": {"width": 1920, "height": 1080},
            "base_url": "http://localhost:8080",
            "timeout": 30000,
            "slow_mo": 0,
            "env": {
                "username": "",
                "password": ""
            },
            "login": {
                "url": ""
            },
            "runtime": {
                "test_url_input": "",
                "dashboard_url": "",
                "goods_list_url": "",
                "login_url": "",
                "auth_storage_state_path": "artifacts/auth/backend_storage_state.json",
                "backend_username": "",
                "backend_password": "",
                "auth_bearer_token": "",
                "h5_accounts": [],
                "h5_phones": [],
                "h5_default_phone": "",
                "h5_verification_code": "",
                "pay_password": "",
                "business_origin": "",
                "passport_origin": "",
                "store_id": ""
            },
            "url_mappings": {},
            "allure": {
                "path": "allure",
                "enabled": True
            },
            "paths": {
                "record_raw": "record_raw",
                "page_objects": "page_objects",
                "tests": "tests",
                "test_data": "test_data",
                "allure_results": "allure-results",
                "allure_report": "allure-report"
            },
            "environment": {
                "auto_install_missing": True,
                "sync_exact_versions": False,
                "ensure_playwright_browser": True
            }
        }

    def get(self, key, default=None):
        keys = key.split(".")
        value = self.config
        for k in keys:
            value = value.get(k, default)
            if value is None:
                return default
        return value

    def _deep_merge(self, base, override):
        if not isinstance(base, dict) or not isinstance(override, dict):
            return override

        merged = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def save(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)

    def update(self, key, value):
        keys = key.split(".")
        config = self.config
        for i, k in enumerate(keys[:-1]):
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        self.save()

    def replace(self, new_config):
        self.config = self._deep_merge(self._get_default_config(), new_config or {})
        self.save()
