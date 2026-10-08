import os
import re
from pathlib import Path
from collections import defaultdict

from common.environment_manager import EnvironmentManager
from common.script_parser import ScriptParser

EnvironmentManager(Path(__file__).parent).prepare_environment(
    auto_install_missing=True,
    sync_exact_versions=False,
    ensure_playwright_browser=False,
    ensure_config=True,
)

import yaml

from common.config_manager import ConfigManager


class FrameworkTransformer:
    def __init__(self):
        self.config = ConfigManager()
        self.project_root = Path(__file__).parent
        self.record_raw_dir = self.project_root / self.config.get("paths.record_raw", "record_raw")
        self.page_objects_dir = self.project_root / self.config.get("paths.page_objects", "page_objects")
        self.tests_dir = self.project_root / self.config.get("paths.tests", "tests")
        self.test_data_dir = self.project_root / self.config.get("paths.test_data", "test_data")
        
        self.parser = ScriptParser(self.record_raw_dir)
        self.parsed_scripts = []
        self.page_objects = {}
        self.test_cases = []
        self.test_data = {}

    def transform(self):
        self.parsed_scripts = self.parser.parse_all_scripts()
        self._print_contract_report()
        
        if not self.parsed_scripts:
            self._cleanup_stale_generated_outputs()
            print("No valid scripts found in record_raw directory.")
            return
        
        self.parser.extract_common_actions()
        self.page_objects = self.parser.generate_page_objects()
        self.test_cases = self.parser.generate_test_cases()
        self.test_data = self.parser.generate_test_data()
        
        self._generate_common_base_methods()
        self._generate_page_object_files()
        self._generate_test_case_files()
        self._generate_test_data_files()
        self._cleanup_stale_generated_outputs()
        
        print(f"Framework transformation completed successfully!")
        print(f"- Parsed {len(self.parsed_scripts)} scripts")
        print(f"- Generated {len(self.page_objects)} page objects")
        print(f"- Generated {len(self.test_cases)} test cases")

    def _cleanup_stale_generated_outputs(self):
        active_modules = {script["module_name"] for script in self.parsed_scripts}
        skipped_modules = set(getattr(self.parser, "skipped_support_modules", []))
        invalid_modules = set(getattr(self.parser, "invalid_script_modules", []))
        invalid_modules.update(getattr(self.parser, "invalid_contract_modules", []))

        for test_file in self.tests_dir.glob("test_*.py"):
            raw_info = self._extract_generated_raw_wrapper_info(test_file)
            if not raw_info:
                continue

            module_name = raw_info["module_name"]
            raw_path = raw_info["raw_path"]
            remove_reason = None

            if module_name in skipped_modules:
                remove_reason = "support module"
            elif module_name in invalid_modules:
                remove_reason = "invalid raw script"
            elif not raw_path.exists():
                remove_reason = "missing raw script"
            elif module_name not in active_modules:
                remove_reason = "stale generated wrapper"

            if not remove_reason:
                continue

            data_file = self.test_data_dir / f"{module_name}.yaml"
            data_file_exists = data_file.exists()
            test_file.unlink(missing_ok=True)
            data_file.unlink(missing_ok=True)
            print(f"Removed {remove_reason} test: {test_file}")
            if data_file_exists:
                print(f"Removed {remove_reason} test data: {data_file}")

    def _print_contract_report(self):
        error_items = getattr(self.parser, "framework_contract_errors", [])
        warning_items = getattr(self.parser, "framework_contract_warnings", [])
        if not error_items and not warning_items:
            return

        print("Framework contract check:")
        for item in error_items:
            errors = "；".join(item.get("errors", []))
            warnings = item.get("warnings", [])
            warning_text = f"；warnings: {'；'.join(warnings)}" if warnings else ""
            print(f"- ERROR {item.get('module_name')} ({item.get('file_name')}): {errors}{warning_text}")

        for item in warning_items:
            warnings = "；".join(item.get("warnings", []))
            print(f"- WARN  {item.get('module_name')} ({item.get('file_name')}): {warnings}")

    def _extract_generated_raw_wrapper_info(self, test_file):
        try:
            content = test_file.read_text(encoding="utf-8")
        except Exception:
            return None

        if "@pytest.mark.raw_script_wrapper" not in content:
            return None

        match = re.search(
            r'RAW_SCRIPT_PATH = Path\(__file__\)\.parent\.parent / "record_raw" / "([^"]+)"',
            content,
        )
        if not match:
            return None

        raw_file_name = match.group(1)
        raw_path = self.record_raw_dir / raw_file_name
        return {
            "module_name": Path(raw_file_name).stem,
            "raw_path": raw_path,
        }

    def _generate_common_base_methods(self):
        pass

    def _extract_common_methods(self):
        methods = []
        common_actions = self.parser.common_actions
        
        login_actions = [a for a in common_actions if any(kw in a.lower() for kw in ["login", "username", "password"])]
        if login_actions:
            methods.append(self._generate_login_method(login_actions))
        
        nav_actions = [a for a in common_actions if any(kw in a.lower() for kw in ["goto", "navigate"])]
        if nav_actions:
            methods.append(self._generate_navigate_method(nav_actions))
        
        return methods

    def _generate_login_method(self, actions):
        method_code = '''    def login(self, username, password):
        with allure.step(f"Login with username: {username}"):
'''
        for action in actions:
            line = action
            line = re.sub(r'page\.locator\(', 'self.page.locator(', line)
            line = re.sub(r'page\.fill\(', 'self.page.fill(', line)
            line = re.sub(r'page\.click\(', 'self.page.click(', line)
            if 'username' in line.lower():
                line = re.sub(r'["\'].*?["\']', '"{username}"', line)
            elif 'password' in line.lower():
                line = re.sub(r'["\'].*?["\']', '"{password}"', line)
            method_code += f"        {line}\n"
        
        method_code += "        self.wait_for_load_state()\n"
        return method_code

    def _generate_navigate_method(self, actions):
        method_code = '''    def navigate_to(self, path):
        with allure.step(f"Navigate to path: {path}"):
            base_url = os.getenv("BASE_URL", "http://localhost:8080")
            self.page.goto(f"{base_url}{path}")
            self.wait_for_load_state()
'''
        return method_code

    def _generate_page_object_files(self):
        for page_name, actions in self.page_objects.items():
            if page_name == "BasePage":
                continue
            
            file_name = page_name.lower().replace("page", "_page") + ".py"
            file_path = self.page_objects_dir / file_name
            
            po_content = self._generate_page_object_content(page_name, actions)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(po_content)
            
            print(f"Generated page object: {file_path}")
            
            self._update_page_objects_init(page_name)

    def _generate_page_object_content(self, page_name, actions):
        content = f'''from playwright.sync_api import Page
import allure
from .base_page import BasePage


class {page_name}(BasePage):
    def __init__(self, page: Page):
        super().__init__(page)
'''
        
        methods = self._group_actions_into_methods(actions)
        for method_name, method_actions in methods.items():
            simple_lines = []
            for action in method_actions:
                if not self._is_simple_page_object_action(action):
                    continue
                line = action["source_line"]
                line = self._transform_action_line_for_po(line)
                simple_lines.append(line)

            if not simple_lines:
                continue

            content += f"\n    def {method_name}(self, data=None):\n"
            content += f"        with allure.step(\"{method_name.replace('_', ' ').capitalize()}\"):\n"
            for line in simple_lines:
                content += f"            {line}\n"
        
        return content

    def _transform_action_line_for_po(self, line):
        line = re.sub(r'page\.locator\(', 'self.page.locator(', line)
        line = re.sub(r'page\.fill\(', 'self.page.fill(', line)
        line = re.sub(r'page\.click\(', 'self.page.click(', line)
        line = re.sub(r'page\.type\(', 'self.page.type(', line)
        line = re.sub(r'page\.wait_for_selector\(', 'self.page.wait_for_selector(', line)
        line = re.sub(r'page\.wait_for_url\(', 'self.page.wait_for_url(', line)
        line = re.sub(r'page\.wait_for_load_state\(', 'self.page.wait_for_load_state(', line)
        return line

    def _group_actions_into_methods(self, actions):
        methods = defaultdict(list)
        
        for idx, action in enumerate(actions):
            method_name = self._infer_method_name(action, idx)
            methods[method_name].append(action)
        
        return dict(methods)

    def _infer_method_name(self, action, index):
        method_name = action["method_name"]
        source = action["source_line"].lower()
        
        if "click" in method_name:
            if "button" in source or "btn" in source:
                return "click_button"
            elif "submit" in source:
                return "submit_form"
            elif "login" in source:
                return "click_login_button"
            elif "search" in source:
                return "click_search_button"
            else:
                return f"click_element_{index}"
        elif "fill" in method_name or "type" in method_name:
            if "username" in source:
                return "fill_username"
            elif "password" in source:
                return "fill_password"
            elif "search" in source:
                return "fill_search_input"
            else:
                return f"fill_input_{index}"
        elif "select" in method_name:
            return f"select_option_{index}"
        elif "wait" in method_name:
            return f"wait_for_{index}"
        else:
            return f"action_{index}"

    def _transform_action_line(self, line):
        line = re.sub(r'page\.locator\(', 'self.page.locator(', line)
        line = re.sub(r'page\.fill\(', 'self.page.fill(', line)
        line = re.sub(r'page\.click\(', 'self.page.click(', line)
        line = re.sub(r'page\.type\(', 'self.page.type(', line)
        line = re.sub(r'page\.wait_for_selector\(', 'self.page.wait_for_selector(', line)
        line = re.sub(r'page\.wait_for_url\(', 'self.page.wait_for_url(', line)
        line = re.sub(r'page\.wait_for_load_state\(', 'self.page.wait_for_load_state(', line)

        transformed_input_line = self._replace_input_action_value(line)
        if transformed_input_line != line:
            return transformed_input_line

        for param_name, original_value in self._extract_params_from_line(line):
            if len(original_value) > 50:
                continue
            if original_value in ["button", "link", "textbox", "checkbox", "radio", "option", "dialog", "menu", "menuitem", "row", "cell"]:
                continue
            line = re.sub(r'["\']' + re.escape(original_value) + r'["\']', f"data['parameters']['{param_name}']", line)
        
        return line

    def _transform_action_line_for_test(self, line):
        line = self._normalize_anchor_text_click(line)
        line = self._replace_goto_target(line)
        line = self._replace_input_action_value(line)
        line = self._replace_press_value(line)
        line = self._replace_has_text_value(line)
        line = self._replace_expect_text_value(line)
        line = self._replace_assert_text_value(line)
        return line

    def _normalize_anchor_text_click(self, line):
        pattern = r'(\w+)\.locator\("a"\)\.filter\(has_text=(["\'])([^"\']+)\2\)\.click\(\)'
        match = re.search(pattern, line)
        if not match:
            return line

        page_var = match.group(1)
        text = match.group(3)
        replacement = f'{page_var}.get_by_text("{text}", exact=True).first.click()'
        return line[:match.start()] + replacement + line[match.end():]

    def _replace_input_action_value(self, line):
        for action_name in ["fill", "type"]:
            pattern = rf'(\.{action_name}\()(["\'])([^"\']+)\2'
            match = re.search(pattern, line)
            if not match:
                continue

            value = match.group(3)
            if value.isdigit():
                return line
            if value.startswith("http"):
                return line

            cleaned = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', value).strip('_')
            if not cleaned:
                return line

            param_name = "param_" + cleaned
            replacement = f"{match.group(1)}data['parameters']['{param_name}']"
            return line[:match.start()] + replacement + line[match.end():]

        return line

    def _is_selector(self, value):
        selector_patterns = [
            r'^#',      
            r'^\.',     
            r'^\[',     
            r'^button\[',
            r'^input\[',
            r'^a\[',
            r'^div\[',
            r'^span\[',
            r'^\*\[',
            r'^text=',
            r'^role=',
            r'^data-',
        ]
        for pattern in selector_patterns:
            if re.match(pattern, value):
                return True
        return False

    def _extract_params_from_line(self, line):
        params = []
        is_input_line = ".fill(" in line or ".type(" in line
        string_pattern = r'["\']([^"\']+)["\']'
        matches = re.findall(string_pattern, line)
        for match in matches:
            if match.isdigit():
                continue
            if match.startswith("http"):
                continue
            if self._is_selector(match):
                continue
            if match in ["Tab", "Enter", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]:
                param_name = f"param_{match}"
                params.append((param_name, match))
                continue
            if match in ["button", "link", "textbox", "checkbox", "radio", "option", "dialog", "menu", "menuitem", "row", "cell", "div", "span", "a", "input", "form", "table", "tr", "td", "th"]:
                continue
            if re.match(r'^\d+/\d+$', match):
                continue
            if re.match(r'^[\^$].*[\^$]$', match):
                continue
            if len(match) > 30:
                continue
            if not is_input_line and re.match(r'.*(像素|建议|尺寸|提示|请输入|分类|标签|客户|保存|保 存|新增|登录|密码|用户名|一级|visible|enabled).*', match):
                continue
            if len(match) >= 2:
                cleaned = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', match).strip('_')
                param_name = "param_" + cleaned
                if param_name != "param_" and len(param_name) <= 100:
                    params.append((param_name, match))
        return params

    def _replace_goto_target(self, line):
        pattern = r'(\w+)\.goto\((["\'])(https?://[^"\']+)\2\)'
        match = re.search(pattern, line)
        if not match:
            return line

        page_var = match.group(1)
        target_url = match.group(3)
        replacement = (
            f"{page_var}.goto(resolve_target_url(\"{target_url}\", config))"
        )
        return line[:match.start()] + replacement + line[match.end():]

    def _replace_press_value(self, line):
        pattern = r'(\.press\()(["\'])([^"\']+)\2'
        match = re.search(pattern, line)
        if not match:
            return line

        value = match.group(3)
        if value not in ["Tab", "Enter", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]:
            return line

        param_name = f"param_{value}"
        replacement = f"{match.group(1)}data['parameters']['{param_name}']"
        return line[:match.start()] + replacement + line[match.end():]

    def _replace_expect_text_value(self, line):
        for matcher in ["to_contain_text", "to_have_text", "to_have_value"]:
            pattern = rf'(\.{matcher}\()(["\'])([^"\']+)\2'
            match = re.search(pattern, line)
            if not match:
                continue

            value = match.group(3)
            if value.isdigit():
                return line
            cleaned = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', value).strip('_')
            if not cleaned:
                return line

            param_name = "param_" + cleaned
            replacement = f"{match.group(1)}data['parameters']['{param_name}']"
            return line[:match.start()] + replacement + line[match.end():]
        return line

    def _replace_has_text_value(self, line):
        pattern = r'locator\((["\'])(.*?):has-text\("([^"]+)"\)(.*?)\1\)'
        match = re.search(pattern, line)
        if not match:
            return line

        outer_quote = match.group(1)
        prefix = match.group(2)
        value = match.group(3)
        suffix = match.group(4)
        if value.isdigit():
            return line
        cleaned = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', value).strip('_')
        if not cleaned:
            return line

        param_name = "param_" + cleaned
        if outer_quote == "'":
            replacement = (
                "locator(f'"
                + prefix
                + ':has-text("{data["parameters"]["'
                + param_name
                + '"]}")'
                + suffix
                + "')"
            )
        else:
            replacement = (
                'locator(f"'
                + prefix
                + ":has-text('{data['parameters']['"
                + param_name
                + "']}')"
                + suffix
                + '")'
            )
        return line[:match.start()] + replacement + line[match.end():]

    def _replace_assert_text_value(self, line):
        stripped = line.strip()
        if not stripped.startswith("assert "):
            return line

        line = self._replace_assert_contains_literal(line)
        line = self._replace_assert_function_literal(line, "get_by_text")
        line = self._replace_assert_function_literal(line, "to_have_text")
        line = self._replace_assert_function_literal(line, "to_contain_text")
        line = self._replace_assert_has_text_keyword(line)
        return line

    def _replace_assert_contains_literal(self, line):
        pattern = r'(assert\s+)(["\'])([^"\']+)\2(\s+in\s+)'
        match = re.search(pattern, line)
        if not match:
            return line

        value = match.group(3)
        replacement = (
            match.group(1)
            + self._parameter_expr(value)
            + match.group(4)
        )
        return line[:match.start()] + replacement + line[match.end():]

    def _replace_assert_function_literal(self, line, function_name):
        pattern = rf'(\.{function_name}\()(["\'])([^"\']+)\2'
        match = re.search(pattern, line)
        if not match:
            return line

        value = match.group(3)
        replacement = match.group(1) + self._parameter_expr(value)
        return line[:match.start()] + replacement + line[match.end():]

    def _replace_assert_has_text_keyword(self, line):
        pattern = r'(has_text=)(["\'])([^"\']+)\2'
        match = re.search(pattern, line)
        if not match:
            return line

        value = match.group(3)
        replacement = match.group(1) + self._parameter_expr(value)
        return line[:match.start()] + replacement + line[match.end():]

    def _parameter_expr(self, value):
        cleaned = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', value).strip('_')
        if not cleaned:
            return repr(value)
        return f"data['parameters']['param_{cleaned}']"

    def _is_simple_page_object_action(self, action):
        source_line = action["source_line"].strip()
        if action.get("kind") != "call":
            return False
        if not source_line.startswith("page."):
            return False
        if "expect_popup" in source_line or "context.new_page" in source_line:
            return False
        return True

    def _update_page_objects_init(self, page_name):
        init_path = self.page_objects_dir / "__init__.py"
        with open(init_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        import_line = f"from .{page_name.lower().replace('page', '_page')} import {page_name}"
        if import_line not in content:
            content += f"\n{import_line}"
            with open(init_path, "w", encoding="utf-8") as f:
                f.write(content)

    def _generate_test_case_files(self):
        for test_case in self.test_cases:
            file_name = f"test_{test_case['name']}.py"
            file_path = self.tests_dir / file_name
            
            test_content = self._generate_test_case_content(test_case)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(test_content)
            
            print(f"Generated test case: {file_path}")

    def _generate_test_case_content(self, test_case):
        if test_case.get("mode") == "raw_main_wrapper":
            return self._generate_ai_wrapper_test_case_content(test_case)

        is_login_case = test_case.get("is_login_case", False)
        page_fixture = "session_login"
        
        content = f'''import pytest
import allure
import re
from pathlib import Path

from playwright.sync_api import expect
from common.test_data_loader import load_test_data
from common.url_utils import resolve_target_url


test_data_path = Path(__file__).parent.parent / "test_data" / "{test_case['name']}.yaml"
test_data = load_test_data("{test_case['name']}", test_data_path)


@allure.feature("{test_case['description']}")
class Test{test_case['name'].title().replace("_", "")}:

    @allure.story("Execute recorded test steps")
    @pytest.mark.parametrize("data", [test_data])
    def test_{test_case['name']}(self, {page_fixture}, context, browser, data, config):
        """{test_case['description']}"""
        allure.dynamic.title(f"Test {test_case['name']}")
        page = {page_fixture}
'''

        if is_login_case:
            content += "\n        with allure.step(\"Verify logged in session\"):\n"
            content += "            assert \"passportnew-dev.xiaokeduo.com/#/login\" not in page.url, \"登录后仍停留在登录页\"\n"
            content += "            assert page.locator(\"body\").count() == 1\n"
            return content

        content += "\n        with allure.step(\"Execute business actions\"):\n"
        for action in test_case["business_actions"]:
            if self._should_skip_test_action(action):
                continue
            line = action["source_line"]
            line = self._transform_action_line_for_test(line)
            line = line.replace('.wait_for(state="enabled")', '.wait_for(state="visible")')
            indent = " " * (12 + action.get("indent_level", 0) * 4)
            content += self._indent_block(line, indent) + "\n"
            popup_var = self._extract_popup_page_var(line)
            if popup_var:
                content += f"{indent}{popup_var}.set_default_timeout(config.get(\"timeout\", 30000))\n"

        if test_case["assertions"]:
            content += "\n        with allure.step(\"Verify results\"):\n"
            for assertion in test_case["assertions"]:
                line = assertion["source_line"]
                line = self._transform_action_line_for_test(line)
                indent = " " * (12 + assertion.get("indent_level", 0) * 4)
                content += self._indent_block(line, indent) + "\n"

        return content

    def _generate_ai_wrapper_test_case_content(self, test_case):
        raw_file_name = test_case["raw_file_name"]
        cli_options = test_case.get("cli_options", [])

        content = f'''import os
import subprocess
import sys
from pathlib import Path

import pytest
import allure


RAW_SCRIPT_PATH = Path(__file__).parent.parent / "record_raw" / "{raw_file_name}"
CLI_OPTIONS = {repr(cli_options)}


def _write_temp_png(tmp_path: Path) -> Path:
    png_bytes = (
        b"\\x89PNG\\r\\n\\x1a\\n"
        b"\\x00\\x00\\x00\\rIHDR"
        b"\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x01"
        b"\\x08\\x06\\x00\\x00\\x00\\x1f\\x15\\xc4\\x89"
        b"\\x00\\x00\\x00\\x0cIDATx\\x9cc``\\xf8\\xcf\\xc0\\x00\\x00\\x03\\x01\\x01\\x00\\xc9\\xfe\\x92\\xef"
        b"\\x00\\x00\\x00\\x00IEND\\xaeB`\\x82"
    )
    image_path = tmp_path / "auto_image.png"
    image_path.write_bytes(png_bytes)
    return image_path


def _build_cli_args(tmp_path):
    cli_args = []

    requested_headless = os.getenv("TEST_HEADLESS", "false").lower() == "true"
    has_headless = any(option.get("flag") == "--headless" for option in CLI_OPTIONS)
    has_headed = any(option.get("flag") == "--headed" for option in CLI_OPTIONS)

    if requested_headless and has_headless:
        cli_args.append("--headless")
    elif not requested_headless and has_headed:
        cli_args.append("--headed")

    for option in CLI_OPTIONS:
        flag = option.get("flag")
        action = option.get("action")
        default_value = option.get("default")
        if not flag:
            continue
        if flag in ("--headless", "--headed"):
            continue
        if action == "store_true":
            if bool(default_value):
                cli_args.append(flag)
            continue
        if action == "store_false":
            if default_value is False:
                cli_args.append(flag)
            continue
        if flag == "--image" and default_value is None:
            cli_args.extend(["--image", str(_write_temp_png(tmp_path))])
            continue
        if default_value is None or default_value == "":
            continue

        cli_args.extend([flag, str(default_value)])

    return cli_args


test_data_path = Path(__file__).parent.parent / "test_data" / "{test_case['name']}.yaml"
test_data = load_test_data("{test_case['name']}", test_data_path)


@allure.feature("{test_case['description']}")
class Test{test_case['name'].title().replace("_", "")}:

    @allure.story("Execute raw AI-generated script")
    @pytest.mark.raw_script_wrapper
    @pytest.mark.timeout(3605)
    @pytest.mark.parametrize("data", [test_data])
    def test_{test_case['name']}(self, data, tmp_path):
        """{test_case['description']}"""
        allure.dynamic.title("Test {test_case['name']}")

        with allure.step("Execute raw script main()"):
            cli_args = _build_cli_args(data, tmp_path)
            command = [sys.executable, str(RAW_SCRIPT_PATH), *cli_args]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            result = subprocess.run(
                command,
                cwd=str(Path(__file__).parent.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=3600,
            )

            allure.attach("\\n".join(command), name="raw_command", attachment_type=allure.attachment_type.TEXT)
            if result.stdout:
                allure.attach(result.stdout, name="raw_stdout", attachment_type=allure.attachment_type.TEXT)
            if result.stderr:
                allure.attach(result.stderr, name="raw_stderr", attachment_type=allure.attachment_type.TEXT)

            assert result.returncode == 0, (
                f"raw script exited with code {{result.returncode}}\\n"
                f"STDOUT:\\n{{result.stdout}}\\nSTDERR:\\n{{result.stderr}}"
            )
'''
        return content

    def _generate_ai_wrapper_test_case_content(self, test_case):
        raw_file_name = test_case["raw_file_name"]
        cli_options = test_case.get("cli_options", [])

        content = f'''import os
import subprocess
import sys
from pathlib import Path

import pytest
import allure


RAW_SCRIPT_PATH = Path(__file__).parent.parent / "record_raw" / "{raw_file_name}"
CLI_OPTIONS = {repr(cli_options)}


def _write_temp_png(tmp_path: Path) -> Path:
    png_bytes = (
        b"\\x89PNG\\r\\n\\x1a\\n"
        b"\\x00\\x00\\x00\\rIHDR"
        b"\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x01"
        b"\\x08\\x06\\x00\\x00\\x00\\x1f\\x15\\xc4\\x89"
        b"\\x00\\x00\\x00\\x0cIDATx\\x9cc``\\xf8\\xcf\\xc0\\x00\\x00\\x03\\x01\\x01\\x00\\xc9\\xfe\\x92\\xef"
        b"\\x00\\x00\\x00\\x00IEND\\xaeB`\\x82"
    )
    image_path = tmp_path / "auto_image.png"
    image_path.write_bytes(png_bytes)
    return image_path


def _build_cli_args(tmp_path):
    cli_args = []
    requested_headless = os.getenv("TEST_HEADLESS", "false").lower() == "true"
    has_headless = any(option.get("flag") == "--headless" for option in CLI_OPTIONS)
    has_headed = any(option.get("flag") == "--headed" for option in CLI_OPTIONS)

    if requested_headless and has_headless:
        cli_args.append("--headless")
    elif not requested_headless and has_headed:
        cli_args.append("--headed")

    for option in CLI_OPTIONS:
        flag = option.get("flag")
        action = option.get("action")
        default_value = option.get("default")
        if not flag or flag in ("--headless", "--headed"):
            continue
        if action == "store_true":
            if bool(default_value):
                cli_args.append(flag)
            continue
        if action == "store_false":
            if default_value is False:
                cli_args.append(flag)
            continue
        if flag == "--image" and default_value is None:
            cli_args.extend(["--image", str(_write_temp_png(tmp_path))])
            continue
        if default_value is None or default_value == "":
            continue
        cli_args.extend([flag, str(default_value)])

    return cli_args


@allure.feature("{test_case['description']}")
class Test{test_case['name'].title().replace("_", "")}:

    @allure.story("Execute raw AI-generated script")
    @pytest.mark.raw_script_wrapper
    @pytest.mark.timeout(3605)
    def test_{test_case['name']}(self, tmp_path):
        """{test_case['description']}"""
        allure.dynamic.title("Test {test_case['name']}")

        with allure.step("Execute raw script main()"):
            cli_args = _build_cli_args(tmp_path)
            command = [sys.executable, str(RAW_SCRIPT_PATH), *cli_args]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            result = subprocess.run(
                command,
                cwd=str(Path(__file__).parent.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=3600,
            )

            allure.attach("\\n".join(command), name="raw_command", attachment_type=allure.attachment_type.TEXT)
            if result.stdout:
                allure.attach(result.stdout, name="raw_stdout", attachment_type=allure.attachment_type.TEXT)
            if result.stderr:
                allure.attach(result.stderr, name="raw_stderr", attachment_type=allure.attachment_type.TEXT)

            assert result.returncode == 0, (
                f"raw script exited with code {{result.returncode}}\\n"
                f"STDOUT:\\n{{result.stdout}}\\nSTDERR:\\n{{result.stderr}}"
            )
'''
        return content

    def _generate_ai_wrapper_test_case_content(self, test_case):
        raw_file_name = test_case["raw_file_name"]
        cli_options = test_case.get("cli_options", [])

        content = f'''import importlib.util
import os
import sys
import traceback
from pathlib import Path

import pytest
import allure

from common.raw_wrapper_support import (
    attach_raw_failure_report,
    build_raw_failure_report,
    format_raw_failure_report,
    tee_std_streams,
)
from common.runtime_config import apply_runtime_config_to_raw_module, merge_runtime_parameters
from common.test_data_loader import load_test_data


RAW_SCRIPT_PATH = Path(__file__).parent.parent / "record_raw" / "{raw_file_name}"
TEST_DATA_PATH = Path(__file__).parent.parent / "test_data" / "{test_case['name']}.yaml"
CLI_OPTIONS = {repr(cli_options)}
TEST_DATA = merge_runtime_parameters(
    load_test_data("{test_case['name']}", TEST_DATA_PATH),
    CLI_OPTIONS,
)


def _load_raw_module():
    if not RAW_SCRIPT_PATH.exists():
        pytest.skip(f"raw script not found: {{RAW_SCRIPT_PATH}}")
    spec = importlib.util.spec_from_file_location("raw_{test_case['name']}", RAW_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return apply_runtime_config_to_raw_module(module)


def _write_temp_png(tmp_path: Path) -> Path:
    png_bytes = (
        b"\\x89PNG\\r\\n\\x1a\\n"
        b"\\x00\\x00\\x00\\rIHDR"
        b"\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x01"
        b"\\x08\\x06\\x00\\x00\\x00\\x1f\\x15\\xc4\\x89"
        b"\\x00\\x00\\x00\\x0cIDATx\\x9cc``\\xf8\\xcf\\xc0\\x00\\x00\\x03\\x01\\x01\\x00\\xc9\\xfe\\x92\\xef"
        b"\\x00\\x00\\x00\\x00IEND\\xaeB`\\x82"
    )
    image_path = tmp_path / "auto_image.png"
    image_path.write_bytes(png_bytes)
    return image_path


def _build_cli_args(tmp_path):
    params = (TEST_DATA or {{}}).get("parameters", {{}}) or {{}}
    cli_args = []

    requested_headless = os.getenv("TEST_HEADLESS", "false").lower() == "true"
    has_headless = any(option.get("flag") == "--headless" for option in CLI_OPTIONS)
    has_headed = any(option.get("flag") == "--headed" for option in CLI_OPTIONS)

    if requested_headless and has_headless:
        cli_args.append("--headless")
    elif not requested_headless and has_headed:
        cli_args.append("--headed")

    for option in CLI_OPTIONS:
        flag = option.get("flag")
        action = option.get("action")
        default_value = option.get("default")
        dest = option.get("dest")
        if not flag or flag in ("--headless", "--headed"):
            continue

        if dest == "image" and dest not in params:
            cli_args.extend(["--image", str(_write_temp_png(tmp_path))])
            continue

        if dest not in params:
            continue

        value = params[dest]
        if action == "store_true":
            if bool(value):
                cli_args.append(flag)
            continue
        if action == "store_false":
            if value is False:
                cli_args.append(flag)
            continue
        if value is None or value == "":
            if default_value is None and dest == "image":
                cli_args.extend(["--image", str(_write_temp_png(tmp_path))])
            continue

        cli_args.extend([flag, str(value)])

    return cli_args


def _build_playwright_proxy(context, shared_page):
    class _PageProxy:
        def __init__(self, real_page):
            self._real_page = real_page

        def __getattr__(self, item):
            return getattr(self._real_page, item)

    class _ContextProxy:
        def __init__(self, shared_context, initial_page=None):
            self._shared_context = shared_context
            self._initial_page = initial_page
            self._initial_page_claimed = False
            self._pages = []

        @property
        def pages(self):
            return list(self._shared_context.pages)

        def new_page(self, *args, **kwargs):
            if not self._initial_page_claimed and self._initial_page is not None:
                self._initial_page_claimed = True
                page = _PageProxy(self._initial_page)
                self._pages.append((page, False))
                return page

            page = _PageProxy(self._shared_context.new_page())
            self._pages.append((page, True))
            return page

        def close(self):
            for page, should_close in list(self._pages):
                if not should_close:
                    continue
                try:
                    page.close()
                except Exception:
                    pass
            self._pages.clear()

    class _BrowserProxy:
        def __init__(self, shared_context, initial_page):
            self._shared_context = shared_context
            self._initial_page = initial_page
            self._initial_page_claimed = False
            self._contexts = []

        def _claim_initial_page(self):
            if self._initial_page_claimed or self._initial_page is None:
                return None
            self._initial_page_claimed = True
            return self._initial_page

        def new_page(self, *args, **kwargs):
            ctx = _ContextProxy(
                self._shared_context,
                initial_page=self._claim_initial_page(),
            )
            self._contexts.append(ctx)
            return ctx.new_page(*args, **kwargs)

        def new_context(self, *args, **kwargs):
            ctx = _ContextProxy(
                self._shared_context,
                initial_page=self._claim_initial_page(),
            )
            self._contexts.append(ctx)
            return ctx

        def close(self):
            for ctx in list(self._contexts):
                try:
                    ctx.close()
                except Exception:
                    pass
            self._contexts.clear()

    class _BrowserTypeProxy:
        def __init__(self, shared_context, initial_page):
            self._shared_context = shared_context
            self._initial_page = initial_page

        def launch(self, *args, **kwargs):
            return _BrowserProxy(self._shared_context, self._initial_page)

    class _PlaywrightProxy:
        def __init__(self, shared_context, initial_page):
            self.chromium = _BrowserTypeProxy(shared_context, initial_page)
            self.firefox = _BrowserTypeProxy(shared_context, initial_page)
            self.webkit = _BrowserTypeProxy(shared_context, initial_page)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    return _PlaywrightProxy(context, shared_page)


@allure.feature("{test_case['description']}")
class Test{test_case['name'].title().replace("_", "")}:

    @allure.story("Execute raw AI-generated script")
    @pytest.mark.raw_script_wrapper
    @pytest.mark.timeout(3605)
    def test_{test_case['name']}(self, session_login, context, tmp_path, request):
        """Test case generated from {test_case['description']}"""
        allure.dynamic.title("Test {test_case['name']}")

        raw_module = _load_raw_module()
        original_sync_playwright = getattr(raw_module, "sync_playwright", None)
        raw_module.sync_playwright = lambda: _build_playwright_proxy(context, session_login)

        try:
            with allure.step("Execute raw script main()"):
                cli_args = _build_cli_args(tmp_path)
                original_argv = sys.argv[:]
                result = None
                raw_exception = None
                raw_traceback_text = ""
                raw_stdout_text = ""
                raw_stderr_text = ""
                try:
                    with tee_std_streams() as (stdout_buffer, stderr_buffer):
                        try:
                            sys.argv = [str(RAW_SCRIPT_PATH), *cli_args]
                            result = raw_module.main()
                        except Exception as exc:
                            raw_exception = exc
                            raw_traceback_text = traceback.format_exc()
                        finally:
                            raw_stdout_text = stdout_buffer.getvalue()
                            raw_stderr_text = stderr_buffer.getvalue()
                finally:
                    sys.argv = original_argv

                if raw_exception is not None or result not in (0, None):
                    failure_report = build_raw_failure_report(
                        result=result,
                        stdout_text=raw_stdout_text,
                        stderr_text=raw_stderr_text,
                        exception=raw_exception,
                        traceback_text=raw_traceback_text,
                    )
                    request.node._codex_raw_failure = failure_report
                    attach_raw_failure_report(
                        failure_report,
                        stdout_text=raw_stdout_text,
                        stderr_text=raw_stderr_text,
                        traceback_text=raw_traceback_text,
                    )
                    pytest.fail(format_raw_failure_report(failure_report), pytrace=False)
        finally:
            if original_sync_playwright is not None:
                raw_module.sync_playwright = original_sync_playwright
'''
        return content

    def _extract_popup_page_var(self, line):
        match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[A-Za-z_][A-Za-z0-9_]*\.value$', line.strip())
        if match:
            return match.group(1)
        return None

    def _indent_block(self, text, indent):
        return "\n".join(f"{indent}{line}" if line else "" for line in text.splitlines())

    def _should_skip_test_action(self, action):
        source_line = action["source_line"].strip()

        if ".get_by_text(" in source_line and ".click()" in source_line:
            text_match = re.search(r'get_by_text\((["\'])(.*?)\1', source_line)
            if text_match:
                text_value = text_match.group(2)
                if len(text_value) > 20:
                    return True

        return False

    def _generate_test_data_files(self):
        for test_name, data in self.test_data.items():
            file_name = f"{test_name}.yaml"
            file_path = self.test_data_dir / file_name
            
            data_content = {
                "description": f"Test data for {test_name}",
                "parameters": {
                    param_name: self._build_parameter_entry(test_name, param_name, value)
                    for param_name, value in data.items()
                }
            }
            
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(data_content, f, default_flow_style=False, allow_unicode=True)
            
            print(f"Generated test data: {file_path}")

    def _build_parameter_entry(self, test_name, param_name, value):
        if self._should_use_unique_parameter(test_name, param_name, value):
            return {
                "mode": "module_time_hms",
                "value": value,
            }
        return value

    def _should_use_unique_parameter(self, test_name, param_name, value):
        creation_keywords = ["新增", "创建", "create", "add", "new"]
        module_lower = test_name.lower()
        if not any(keyword in test_name or keyword in module_lower for keyword in creation_keywords):
            return False

        if not isinstance(value, str):
            return False

        if value in ["Tab", "Enter", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]:
            return False

        if value in ["networkidle", "domcontentloaded", "load", "commit"]:
            return False

        if param_name.endswith("_prefix"):
            return False

        if value.isdigit() or re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return False

        if len(value) > 20 and "商品" not in value and "标签" not in value and "分类" not in value:
            return False

        return True


if __name__ == "__main__":
    transformer = FrameworkTransformer()
    transformer.transform()
