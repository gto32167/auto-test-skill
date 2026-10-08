import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path


class EnvironmentManager:
    def __init__(self, project_root=None, python_executable=None):
        self.project_root = Path(project_root or Path(__file__).resolve().parent.parent)
        self.python_executable = python_executable or sys.executable
        self.requirements_path = self.project_root / "requirements.txt"
        self.config_path = self.project_root / "config.yaml"

    def prepare_environment(
        self,
        auto_install_missing=True,
        sync_exact_versions=False,
        ensure_playwright_browser=True,
        ensure_config=True,
    ):
        report = self.inspect_environment()
        install_targets = self._get_install_targets(
            report["requirements"], auto_install_missing, sync_exact_versions
        )

        if install_targets:
            self._install_packages(install_targets)
            report = self.inspect_environment()

        if ensure_config:
            report["config_updated"] = self.ensure_config_file()
            report["allure_command"] = self.detect_allure_command()

        if ensure_playwright_browser:
            report["playwright_browser_status"] = self.ensure_playwright_browser()
        else:
            report["playwright_browser_status"] = "skipped"

        report["usable"] = self._is_usable(report)
        return report

    def ensure_package_specs(self, package_specs):
        install_targets = []
        for spec in package_specs:
            name = spec
            required_version = None
            if "==" in spec:
                name, required_version = [part.strip() for part in spec.split("==", 1)]

            installed = self._get_installed_version(name)
            if installed is None:
                install_targets.append(spec)
            elif required_version and installed != required_version:
                install_targets.append(spec)

        if install_targets:
            self._install_packages(install_targets)

        return install_targets

    def inspect_environment(self):
        requirements = self._load_requirements()
        report = {
            "python": {
                "version": sys.version.split()[0],
                "executable": self.python_executable,
                "supported": sys.version_info >= (3, 8),
            },
            "java_available": self._has_java(),
            "requirements": [],
            "allure_command": self.detect_allure_command(),
            "config_exists": self.config_path.exists(),
        }

        for requirement in requirements:
            installed = self._get_installed_version(requirement["name"])
            if installed is None:
                status = "missing"
            elif requirement["version"] and installed != requirement["version"]:
                status = "version_mismatch"
            else:
                status = "ok"

            report["requirements"].append(
                {
                    "name": requirement["name"],
                    "required": requirement["version"],
                    "spec": requirement["spec"],
                    "installed": installed,
                    "status": status,
                }
            )

        return report

    def ensure_config_file(self):
        from common.config_manager import ConfigManager

        config = ConfigManager()
        updated = False

        if not config.config_path.exists():
            config.save()
            updated = True

        detected_allure = self.detect_allure_command()
        current_allure = config.get("allure.path")
        resolved_current = self._resolve_path(current_allure) if current_allure else None

        if detected_allure and resolved_current != Path(detected_allure):
            config.update("allure.path", self._normalize_path_for_config(detected_allure))
            updated = True

        return updated

    def detect_allure_command(self):
        candidates = []
        env_value = os.getenv("ALLURE_PATH")
        if env_value:
            candidates.append(Path(env_value))

        if self.config_path.exists():
            try:
                from common.config_manager import ConfigManager

                config = ConfigManager()
                configured = config.get("allure.path")
                if configured:
                    candidates.append(self._resolve_path(configured))
            except Exception:
                pass

        seen = set()
        for candidate in candidates:
            candidate = Path(candidate)
            normalized = str(candidate).lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.exists():
                return str(candidate)

        return shutil.which("allure")

    def ensure_playwright_browser(self, browser_name=None):
        browser_name = browser_name or self._get_configured_browser()

        try:
            executable_path = self._get_playwright_executable(browser_name)
        except Exception as exc:
            return f"unavailable: {exc}"

        if executable_path and executable_path.exists():
            return f"ok: {browser_name}"

        command = [
            self.python_executable,
            "-m",
            "playwright",
            "install",
            browser_name,
        ]
        result = subprocess.run(
            command,
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"failed to install Playwright browser {browser_name}: {message}")

        return f"installed: {browser_name}"

    def format_report(self, report):
        lines = []
        lines.append(
            f"Python {report['python']['version']} "
            f"({'supported' if report['python']['supported'] else 'unsupported'})"
        )

        missing = [item for item in report["requirements"] if item["status"] == "missing"]
        mismatched = [item for item in report["requirements"] if item["status"] == "version_mismatch"]

        if missing:
            lines.append(
                "Missing packages: "
                + ", ".join(f"{item['name']}=={item['required']}" for item in missing)
            )
        else:
            lines.append("Missing packages: none")

        if mismatched:
            lines.append(
                "Version differences: "
                + ", ".join(
                    f"{item['name']}({item['installed']} -> {item['required']})"
                    for item in mismatched
                )
            )
        else:
            lines.append("Version differences: none")

        lines.append(f"Allure: {report.get('allure_command') or 'not found'}")
        lines.append(f"Java: {'available' if report.get('java_available') else 'not found'}")
        lines.append(f"Playwright browser: {report.get('playwright_browser_status', 'unknown')}")
        return "\n".join(lines)

    def _load_requirements(self):
        requirements = []
        if not self.requirements_path.exists():
            return requirements

        for raw_line in self.requirements_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            name = line
            required_version = None
            if "==" in line:
                name, required_version = [part.strip() for part in line.split("==", 1)]

            requirements.append(
                {
                    "name": name,
                    "version": required_version,
                    "spec": line,
                }
            )

        return requirements

    def _get_install_targets(self, requirements, auto_install_missing, sync_exact_versions):
        targets = []
        for requirement in requirements:
            if requirement["status"] == "missing" and auto_install_missing:
                targets.append(requirement["spec"])
            elif requirement["status"] == "version_mismatch" and sync_exact_versions:
                targets.append(requirement["spec"])
        return targets

    def _install_packages(self, install_targets):
        command = [
            self.python_executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *install_targets,
        ]
        result = subprocess.run(
            command,
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"failed to install dependencies: {message}")

    def _get_installed_version(self, package_name):
        candidates = [package_name, package_name.replace("_", "-"), package_name.replace("-", "_")]
        for candidate in candidates:
            try:
                return package_version(candidate)
            except PackageNotFoundError:
                continue
        return None

    def _has_java(self):
        result = subprocess.run(
            ["java", "-version"],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0

    def _get_playwright_executable(self, browser_name):
        command = [
            self.python_executable,
            "-c",
            (
                "from playwright.sync_api import sync_playwright;"
                "p = sync_playwright().start();"
                f"print(getattr(p, '{browser_name}').executable_path);"
                "p.stop()"
            ),
        ]
        result = subprocess.run(
            command,
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(message)

        output = result.stdout.strip()
        return Path(output) if output else None

    def _get_configured_browser(self):
        browser_name = os.getenv("TEST_BROWSER")
        if browser_name:
            return browser_name

        if self.config_path.exists():
            try:
                from common.config_manager import ConfigManager

                return ConfigManager().get("browser", "chromium")
            except Exception:
                pass

        return "chromium"

    def _resolve_path(self, value):
        path = Path(value)
        if path.is_absolute():
            return path
        return self.project_root / path

    def _normalize_path_for_config(self, value):
        path = Path(value)
        try:
            relative = path.relative_to(self.project_root)
            return relative.as_posix()
        except ValueError:
            return str(path)

    def _is_usable(self, report):
        if not report["python"]["supported"]:
            return False

        missing = any(item["status"] == "missing" for item in report["requirements"])
        if missing:
            return False

        playwright_status = report.get("playwright_browser_status", "")
        if isinstance(playwright_status, str) and playwright_status.startswith("unavailable:"):
            return False

        return True


def print_environment_report(report):
    manager = EnvironmentManager()
    print(manager.format_report(report))
