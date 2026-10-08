import ast
import os
import re
from pathlib import Path
from collections import defaultdict


class ScriptParser:
    def __init__(self, record_raw_dir):
        self.record_raw_dir = Path(record_raw_dir)
        self.parsed_scripts = []
        self.skipped_support_modules = []
        self.invalid_script_modules = []
        self.invalid_contract_modules = []
        self.framework_contract_errors = []
        self.framework_contract_warnings = []
        self.common_actions = []
        self.page_methods = defaultdict(list)
        self.test_cases = []
        self.data_params = defaultdict(list)

    def parse_all_scripts(self):
        self._reset_parse_state()
        for py_file in self.record_raw_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            if self._is_support_module_file(py_file):
                self.skipped_support_modules.append(py_file.stem)
                continue
            script = self._parse_single_script(py_file)
            if script:
                if script.get("support_module"):
                    self.skipped_support_modules.append(script["module_name"])
                    continue
                if script.get("framework_contract_errors"):
                    self.invalid_contract_modules.append(script["module_name"])
                    self.framework_contract_errors.append(
                        {
                            "module_name": script["module_name"],
                            "file_name": script["file_name"],
                            "errors": script["framework_contract_errors"],
                            "warnings": script.get("framework_contract_warnings", []),
                        }
                    )
                    continue
                if script.get("framework_contract_warnings"):
                    self.framework_contract_warnings.append(
                        {
                            "module_name": script["module_name"],
                            "file_name": script["file_name"],
                            "warnings": script["framework_contract_warnings"],
                        }
                    )
                self.parsed_scripts.append(script)
        return self.parsed_scripts

    def _reset_parse_state(self):
        self.parsed_scripts = []
        self.skipped_support_modules = []
        self.invalid_script_modules = []
        self.invalid_contract_modules = []
        self.framework_contract_errors = []
        self.framework_contract_warnings = []
        self.common_actions = []
        self.page_methods = defaultdict(list)
        self.test_cases = []
        self.data_params = defaultdict(list)

    def _is_support_module_file(self, file_path):
        stem = file_path.stem.lower()
        return stem.endswith("_common")

    def _parse_single_script(self, file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            tree = ast.parse(content)
        except SyntaxError:
            self.invalid_script_modules.append(file_path.stem)
            return None

        script_info = {
            "file_name": file_path.name,
            "module_name": file_path.stem,
            "content": content,
            "tree": tree,
            "function_defs": {},
            "constants": {},
            "cli_options": [],
            "ai_script_mode": False,
            "actions": [],
            "all_target_urls": [],
            "imports": [],
            "browser_setup": [],
            "login_actions": [],
            "page_navigations": [],
            "business_actions": [],
            "assertions": [],
            "data_values": {},
            "page_names": set(),
            "main_target_url": None,
            "has_login_prefix": False,
            "support_module": False,
        }

        self._extract_top_level_metadata(script_info)
        self._extract_cli_options(script_info)
        self._detect_ai_script_mode(script_info)
        self._detect_support_module(script_info)
        self._validate_framework_contract(script_info)
        self._extract_imports(script_info)
        self._extract_actions(script_info)
        self._classify_actions(script_info)
        self._extract_data_values(script_info)
        self._extract_page_names(script_info)

        return script_info

    def _extract_top_level_metadata(self, script_info):
        tree = script_info["tree"]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                script_info["function_defs"][node.name] = node
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    continue
                target_name = node.targets[0].id
                value = self._literal_eval_node(node.value)
                if value is not None:
                    script_info["constants"][target_name] = value

    def _extract_cli_options(self, script_info):
        parse_args_node = script_info["function_defs"].get("parse_args")
        if not parse_args_node:
            return

        cli_options = []
        for node in ast.walk(parse_args_node):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
                continue

            flags = []
            for arg in node.args:
                value = self._get_arg_value(arg)
                if isinstance(value, str):
                    flags.append(value)
            if not flags:
                continue

            long_flag = next((flag for flag in flags if flag.startswith("--")), flags[0])
            dest = None
            action = None
            default = None

            for kw in node.keywords:
                if kw.arg == "dest":
                    dest = self._get_arg_value(kw.value)
                elif kw.arg == "action":
                    action = self._get_arg_value(kw.value)
                elif kw.arg == "default":
                    default = self._resolve_default_value(kw.value, script_info["constants"])

            if not dest:
                dest = long_flag.lstrip("-").replace("-", "_")

            cli_options.append(
                {
                    "flag": long_flag,
                    "flags": flags,
                    "dest": dest,
                    "action": action,
                    "default": default,
                }
            )

        script_info["cli_options"] = cli_options

    def _detect_ai_script_mode(self, script_info):
        function_names = set(script_info["function_defs"].keys())
        helper_count = len(function_names - {"main"})
        has_main = "main" in function_names
        has_sync_playwright = "sync_playwright" in script_info["content"]
        has_argparse = "parse_args" in function_names or "argparse.ArgumentParser" in script_info["content"]
        script_info["ai_script_mode"] = has_main and has_sync_playwright and has_argparse and helper_count >= 3

    def _detect_support_module(self, script_info):
        function_names = set(script_info["function_defs"].keys())
        if "main" in function_names:
            script_info["support_module"] = False
            return

        if not function_names:
            script_info["support_module"] = False
            return

        module_name = script_info["module_name"].lower()
        content = script_info["content"]
        has_entry_pattern = any(
            pattern in content
            for pattern in (
                'if __name__ == "__main__"',
                "if __name__ == '__main__'",
                "with sync_playwright()",
                ".launch(",
            )
        )

        helper_only = len(function_names) >= 3 and not has_entry_pattern
        script_info["support_module"] = module_name.endswith("_common") or helper_only

    def _validate_framework_contract(self, script_info):
        function_names = set(script_info["function_defs"].keys())
        content = script_info["content"]

        looks_like_raw_script = (
            "sync_playwright" in content
            or "parse_args" in function_names
            or "main" in function_names
            or self._has_main_entry_guard(script_info["tree"])
        )
        if not looks_like_raw_script:
            script_info["framework_contract_errors"] = []
            script_info["framework_contract_warnings"] = []
            return

        errors = []
        warnings = []

        required_helpers = ("login_backend", "login_to_store")
        screenshot_helpers = ("save_shot", "safe_screenshot", "capture_debug")

        if "main" not in function_names:
            errors.append("缺少 `main()` 入口函数")
        if "parse_args" not in function_names:
            errors.append("缺少 `parse_args()` 参数函数")
        if "sync_playwright" not in content:
            errors.append("缺少 `sync_playwright` 调用")
        if not self._has_main_entry_guard(script_info["tree"]):
            errors.append("缺少 `if __name__ == '__main__': main()` 启动入口")
        if not any(helper in function_names for helper in required_helpers):
            errors.append("缺少登录辅助函数，需命名为 `login_backend` 或 `login_to_store`")
        if not any(helper in function_names for helper in screenshot_helpers):
            warnings.append("建议提供 `save_shot` / `safe_screenshot` / `capture_debug` 之一")

        style_warnings = []
        if "wait_for_timeout(" in content or "time.sleep(" in content:
            style_warnings.append("包含固定等待，建议改为 expect 轮询或可见性判断")
        if re.search(r'locator\(\s*[\"\']\/?\/', content) or "xpath=" in content:
            style_warnings.append("包含 XPath 选择器，建议优先使用 `data-testid` / role / text")
        if re.search(r'get_by_text\([^)]*\)\.first\.click\(', content) or re.search(r'inner_text\(\)', content):
            style_warnings.append("包含直接文本读取断言，建议改为 `expect(locator).to_have_text()`")
        if ".css=" in content or "locator(\"." in content or "locator('." in content:
            style_warnings.append("包含 CSS 类名选择器，建议优先使用 `data-testid`")
        for warning in style_warnings:
            if warning not in warnings:
                warnings.append(warning)

        script_info["framework_contract_errors"] = errors
        script_info["framework_contract_warnings"] = warnings

    def _has_main_entry_guard(self, tree):
        for node in tree.body:
            if not isinstance(node, ast.If):
                continue
            if not self._is_main_guard_test(node.test):
                continue
            if self._contains_main_call(node.body):
                return True
        return False

    def _is_main_guard_test(self, test_node):
        if not isinstance(test_node, ast.Compare):
            return False
        if len(test_node.ops) != 1 or not isinstance(test_node.ops[0], ast.Eq):
            return False
        if not isinstance(test_node.left, ast.Name) or test_node.left.id != "__name__":
            return False
        if len(test_node.comparators) != 1:
            return False
        comparator = test_node.comparators[0]
        if hasattr(ast, "Constant") and isinstance(comparator, ast.Constant):
            return comparator.value == "__main__"
        if isinstance(comparator, ast.Str):
            return comparator.s == "__main__"
        return False

    def _contains_main_call(self, statements):
        for stmt in statements:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "main":
                        return True
                    if (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "sys"
                        and func.attr == "exit"
                    ):
                        for arg in node.args:
                            if isinstance(arg, ast.Call):
                                inner_func = arg.func
                                if isinstance(inner_func, ast.Name) and inner_func.id == "main":
                                    return True
        return False

    def get_framework_contract_report(self):
        return {
            "errors": list(self.framework_contract_errors),
            "warnings": list(self.framework_contract_warnings),
            "invalid_contract_modules": list(self.invalid_contract_modules),
            "skipped_support_modules": list(self.skipped_support_modules),
            "invalid_script_modules": list(self.invalid_script_modules),
        }

    def _resolve_default_value(self, node, constants):
        literal = self._literal_eval_node(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name):
            return constants.get(node.id)
        return None

    def _literal_eval_node(self, node):
        if hasattr(ast, "Constant") and isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Str):
            return node.s
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.NameConstant):
            return node.value
        return None

    def _extract_imports(self, script_info):
        tree = script_info["tree"]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    script_info["imports"].append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = ", ".join(alias.name for alias in node.names)
                script_info["imports"].append(f"from {module} import {names}")

    def _extract_actions(self, script_info):
        content = script_info["content"]
        content = script_info["content"]
        lines = content.split("\n")

        for node in script_info["tree"].body:
            if isinstance(node, ast.FunctionDef):
                self._extract_stmt_sequence(
                    node.body,
                    content,
                    lines,
                    script_info,
                    indent_level=0,
                )

    def _extract_stmt_sequence(self, statements, content, lines, script_info, indent_level):
        for stmt in statements:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                action = self._parse_action(
                    stmt,
                    content,
                    lines,
                    indent_level=indent_level,
                    kind="call",
                )
                if action:
                    script_info["actions"].append(action)
            elif isinstance(stmt, ast.Assign):
                action = self._build_generic_action(
                    stmt,
                    content,
                    lines,
                    method_name="assign",
                    obj_name="assign",
                    indent_level=indent_level,
                    kind="assign",
                )
                if action:
                    script_info["actions"].append(action)
            elif isinstance(stmt, ast.Assert):
                action = self._build_generic_action(
                    stmt,
                    content,
                    lines,
                    method_name="assert",
                    obj_name="assert",
                    indent_level=indent_level,
                    kind="assert",
                )
                if action:
                    script_info["actions"].append(action)
            elif isinstance(stmt, ast.With):
                action = self._build_generic_action(
                    stmt,
                    content,
                    lines,
                    method_name="with",
                    obj_name="with",
                    indent_level=indent_level,
                    kind="with",
                )
                if action:
                    script_info["actions"].append(action)
                self._extract_stmt_sequence(stmt.body, content, lines, script_info, indent_level + 1)
            elif isinstance(stmt, ast.If):
                action = self._build_generic_action(
                    stmt,
                    content,
                    lines,
                    method_name="if",
                    obj_name="if",
                    indent_level=indent_level,
                    kind="if",
                )
                if action:
                    script_info["actions"].append(action)
                self._extract_stmt_sequence(stmt.body, content, lines, script_info, indent_level + 1)
                self._extract_stmt_sequence(stmt.orelse, content, lines, script_info, indent_level + 1)
            elif isinstance(stmt, (ast.For, ast.While, ast.Try)):
                action = self._build_generic_action(
                    stmt,
                    content,
                    lines,
                    method_name=stmt.__class__.__name__.lower(),
                    obj_name=stmt.__class__.__name__.lower(),
                    indent_level=indent_level,
                    kind=stmt.__class__.__name__.lower(),
                )
                if action:
                    script_info["actions"].append(action)
                for body_name in ["body", "orelse", "finalbody"]:
                    self._extract_stmt_sequence(
                        getattr(stmt, body_name, []),
                        content,
                        lines,
                        script_info,
                        indent_level + 1,
                    )
                if isinstance(stmt, ast.Try):
                    for handler in stmt.handlers:
                        handler_action = self._build_generic_action(
                            handler,
                            content,
                            lines,
                            method_name="except",
                            obj_name="except",
                            indent_level=indent_level,
                            kind="except",
                        )
                        if handler_action:
                            script_info["actions"].append(handler_action)
                        self._extract_stmt_sequence(
                            handler.body,
                            content,
                            lines,
                            script_info,
                            indent_level + 1,
                        )

    def _build_generic_action(self, stmt, content, lines, method_name, obj_name, indent_level, kind):
        line_no = stmt.lineno - 1
        if line_no >= len(lines):
            return None

        if kind in {"with", "if", "for", "while", "try", "except"}:
            source_line = lines[line_no].strip()
        else:
            source_line = ast.get_source_segment(content, stmt) or lines[line_no].strip()

        return {
            "method_name": method_name,
            "obj_name": obj_name,
            "args": [],
            "kwargs": {},
            "source_line": source_line,
            "line_no": line_no,
            "indent_level": indent_level,
            "kind": kind,
        }

    def _parse_action(self, node, content, lines, indent_level=0, kind="call"):
        if isinstance(node, ast.Expr):
            call_node = node.value
        else:
            call_node = node

        if not isinstance(call_node, ast.Call):
            return None

        func = call_node.func
        if isinstance(func, ast.Attribute):
            method_name = func.attr
            obj_name = func.value.id if isinstance(func.value, ast.Name) else str(func.value)
        elif isinstance(func, ast.Name):
            method_name = func.id
            obj_name = "global"
        else:
            return None

        args = []
        for arg in call_node.args:
            arg_val = self._get_arg_value(arg)
            args.append(arg_val)

        kwargs = {}
        for kw in call_node.keywords:
            kwargs[kw.arg] = self._get_arg_value(kw.value)

        line_no = call_node.lineno - 1
        source_line = ast.get_source_segment(content, node) or ast.get_source_segment(content, call_node)
        if not source_line:
            source_line = lines[line_no].strip() if line_no < len(lines) else ""

        return {
            "method_name": method_name,
            "obj_name": obj_name,
            "args": args,
            "kwargs": kwargs,
            "source_line": source_line,
            "line_no": line_no,
            "indent_level": indent_level,
            "kind": kind,
        }

    def _get_arg_value(self, arg):
        if hasattr(ast, 'Constant') and isinstance(arg, ast.Constant):
            return arg.value
        elif isinstance(arg, ast.Str):
            return arg.s
        elif isinstance(arg, ast.Num):
            return arg.n
        elif isinstance(arg, ast.NameConstant):
            return arg.value
        elif isinstance(arg, ast.Name):
            return f"${{{arg.id}}}"
        elif isinstance(arg, ast.Attribute):
            return f"{arg.value.id}.{arg.attr}"
        else:
            return None

    def _classify_actions(self, script_info):
        login_keywords = ["username", "password"]
        navigation_keywords = ["page.goto(", ".goto("]
        assert_keywords = ["assert ", "expect("]
        browser_keywords = ["playwright.", "browser =", "browser.close()", "context =", "context.close()"]

        for action in script_info["actions"]:
            source = action["source_line"]
            source_lower = source.lower()
            method_lower = action["method_name"].lower()

            if any(kw in source for kw in assert_keywords):
                script_info["assertions"].append(action)
            elif any(kw in source for kw in login_keywords) and ("fill" in method_lower or "type" in method_lower):
                script_info["login_actions"].append(action)
            elif any(kw in source for kw in navigation_keywords):
                script_info["page_navigations"].append(action)
            elif any(kw in source for kw in browser_keywords):
                script_info["browser_setup"].append(action)
            else:
                script_info["business_actions"].append(action)

    def _extract_data_values(self, script_info):
        for action in script_info["actions"]:
            for arg in action["args"]:
                if arg is not None and isinstance(arg, str) and len(arg) > 0 and not arg.startswith("$"):
                    self._extract_param(script_info, arg, action)
            for _, value in action["kwargs"].items():
                if value is not None and isinstance(value, str) and len(value) > 0 and not value.startswith("$"):
                    self._extract_param(script_info, value, action)

    def _extract_param(self, script_info, value, action=None):
        if value is None:
            return
        method_name = (action or {}).get("method_name", "").lower()
        is_input_value = method_name in {"fill", "type"}
        if method_name in {"print", "wait_for_load_state"}:
            return
        if value.isdigit():
            return
        if len(value) < 2:
            return
        if re.match(r"^https?://", value):
            return
        if value in ["Tab", "Enter", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]:
            param_name = f"param_{value}"
            script_info["data_values"][param_name] = value
            return
        if value in ["button", "link", "textbox", "checkbox", "radio", "option", "dialog", "menu", "menuitem", "row", "cell", "div", "span", "a", "input", "form", "table", "tr", "td", "th"]:
            return
        if re.match(r'^\d+/\d+$', value):
            return
        if re.match(r'^[\^$].*[\^$]$', value):
            return
        if len(value) > 30:
            return
        if not is_input_value and re.match(r'.*(像素|建议|尺寸|提示|请输入|分类|标签|客户|保存|保 存|新增|登录|密码|用户名|一级|visible|enabled).*', value):
            return
        if not is_input_value and len(value) < 3:
            return
        param_name = self._generate_param_name(value)
        script_info["data_values"][param_name] = value

    def _generate_param_name(self, value):
        clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]", "_", value)
        clean = re.sub(r"_+", "_", clean).strip("_")
        if len(clean) > 30:
            clean = clean[:30]
        return f"param_{clean}"

    def _extract_page_names(self, script_info):
        url_pattern = r'page\.goto\(["\'](.*?)["\']\)'
        content = script_info["content"]
        matches = re.findall(url_pattern, content)
        if matches:
            script_info["target_url"] = matches[0]
            script_info["all_target_urls"] = matches
        for url in matches:
            path = url.split("/")[-1] if "/" in url else url
            page_name = self._clean_page_name(path)
            if page_name:
                script_info["page_names"].add(page_name)

    def _clean_page_name(self, name):
        name = re.sub(r"\.html?$", "", name)
        name = re.sub(r"\?.*", "", name)
        name = re.sub(r"[^a-zA-Z0-9]", "_", name)
        return name.lower()

    def extract_common_actions(self):
        action_counts = defaultdict(int)
        for script in self.parsed_scripts:
            for action in script["login_actions"] + script["page_navigations"] + script["browser_setup"]:
                key = action["source_line"]
                action_counts[key] += 1

        threshold = max(1, len(self.parsed_scripts) // 2)
        for action, count in action_counts.items():
            if count >= threshold:
                self.common_actions.append(action)

        return self.common_actions

    def generate_page_objects(self):
        for script in self.parsed_scripts:
            if script.get("ai_script_mode"):
                continue
            all_actions = script["business_actions"] + script["login_actions"]
            
            page_name = "BasePage"
            if script["page_names"]:
                first_page = list(script["page_names"])[0]
                page_name = first_page.capitalize() + "Page"
            
            for action in all_actions:
                inferred_name = self._infer_page_name(action)
                if inferred_name != "BasePage":
                    page_name = inferred_name
                self.page_methods[page_name].append(action)

        return self.page_methods

    def _infer_page_name(self, action):
        source = action["source_line"]
        if "login" in source.lower():
            return "LoginPage"
        elif "home" in source.lower() or "index" in source.lower():
            return "HomePage"
        elif "search" in source.lower():
            return "SearchPage"
        elif "result" in source.lower():
            return "ResultPage"
        elif "detail" in source.lower():
            return "DetailPage"
        elif "list" in source.lower():
            return "ListPage"
        elif "form" in source.lower():
            return "FormPage"
        else:
            return "BasePage"

    def generate_test_cases(self):
        for script in self.parsed_scripts:
            if script.get("ai_script_mode"):
                test_case = {
                    "name": script["module_name"],
                    "description": f"Test case generated from {script['file_name']}",
                    "page_names": [],
                    "business_actions": [],
                    "assertions": [],
                    "data_values": {},
                    "target_url": script.get("target_url", "http://localhost:8080"),
                    "is_login_case": False,
                    "mode": "raw_main_wrapper",
                    "raw_file_name": script["file_name"],
                    "cli_options": script.get("cli_options", []),
                }
                self.test_cases.append(test_case)
                continue

            execution_actions, assertions, main_target_url, has_login_prefix = self._extract_execution_sections(script)
            is_login_case = (
                "login" in script["module_name"].lower()
                and len(execution_actions) == 0
            )
            script["main_target_url"] = main_target_url
            script["has_login_prefix"] = has_login_prefix
            test_case = {
                "name": script["module_name"],
                "description": f"Test case generated from {script['file_name']}",
                "page_names": list(script["page_names"]),
                "business_actions": execution_actions,
                "assertions": assertions,
                "data_values": script["data_values"],
                "target_url": main_target_url or script.get("target_url", "http://localhost:8080"),
                "is_login_case": is_login_case,
            }
            self.test_cases.append(test_case)

        return self.test_cases

    def _extract_execution_sections(self, script):
        actions = script["actions"]
        start_index, has_login_prefix = self._find_business_start_index(actions, script["module_name"])

        if start_index is None:
            return [], [], None, has_login_prefix

        filtered_actions = []
        main_target_url = None

        for action in actions[start_index:]:
            if self._is_setup_or_teardown(action):
                continue

            source_line = action["source_line"]
            if main_target_url is None:
                url_match = re.search(r'\.goto\(["\'](.*?)["\']\)', source_line)
                if url_match and "passportnew-dev" not in url_match.group(1):
                    main_target_url = url_match.group(1)

            filtered_actions.append(action)

        trailing_assertion_index = len(filtered_actions)
        for idx in range(len(filtered_actions) - 1, -1, -1):
            if self._is_assertion_action(filtered_actions[idx]):
                trailing_assertion_index = idx
                continue
            break

        execution_actions = filtered_actions[:trailing_assertion_index]
        assertions = filtered_actions[trailing_assertion_index:]

        return execution_actions, assertions, main_target_url, has_login_prefix

    def _find_business_start_index(self, actions, module_name):
        module_lower = module_name.lower()
        if "login" in module_lower:
            return None, True

        first_non_setup = None
        login_context_started = False
        seen_password = False
        last_login_related_index = None

        for idx, action in enumerate(actions):
            if self._is_setup_or_teardown(action):
                continue

            if first_non_setup is None:
                first_non_setup = idx

            source_line = action["source_line"]
            if "passportnew-dev" in source_line or "请输入注册时填写的手机号" in source_line:
                login_context_started = True
                last_login_related_index = idx
                continue

            if "请输入密码" in source_line:
                login_context_started = True
                seen_password = True
                last_login_related_index = idx
                continue

            if login_context_started:
                if self._is_login_related_during_prefix(source_line, seen_password):
                    last_login_related_index = idx
                    continue

                if last_login_related_index is not None:
                    return idx, True

        if last_login_related_index is not None:
            return len(actions), True
        return first_non_setup, False

    def _is_login_related_during_prefix(self, source_line, seen_password):
        lowered = source_line.lower()
        if "store-logo" in lowered or "store-info" in lowered:
            return True
        if ".wait_for_timeout(" in lowered:
            return True
        if ".press(\"tab\")" in lowered or ".press('tab')" in lowered:
            return True
        if seen_password and "get_by_role(\"button\")" in lowered and "发布商品" not in lowered:
            return True
        return False

    def _is_setup_or_teardown(self, action):
        source_line = action["source_line"]
        setup_patterns = [
            "playwright.chromium.launch(",
            "playwright.firefox.launch(",
            "playwright.webkit.launch(",
            "browser = ",
            "context = browser.new_context(",
            "page = context.new_page(",
            "context.close()",
            "browser.close()",
            "with sync_playwright()",
        ]
        return any(pattern in source_line for pattern in setup_patterns)

    def _is_assertion_action(self, action):
        source_line = action["source_line"]
        if action["kind"] == "assert":
            return True
        return "assert " in source_line or "expect(" in source_line

    def generate_test_data(self):
        all_data = defaultdict(dict)
        for script in self.parsed_scripts:
            if script.get("ai_script_mode"):
                for option in script.get("cli_options", []):
                    dest = option.get("dest")
                    flag = option.get("flag")
                    default = option.get("default")
                    if not dest or flag == "--headed" or default is None:
                        continue
                    all_data[script["module_name"]][dest] = default
                continue
            for param_name, value in script["data_values"].items():
                all_data[script["module_name"]][param_name] = value
        return all_data
