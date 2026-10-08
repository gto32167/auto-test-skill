import os
import sys
import json
import re
import subprocess
import shutil
import threading
import time
from pathlib import Path
from datetime import datetime

from common.environment_manager import EnvironmentManager, print_environment_report
from common.script_parser import ScriptParser


class TestRunner:
    PYTEST_STATUS_PATTERN = re.compile(
        r"^(tests[\\/].+?::.+?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b"
    )

    def __init__(self, sync_exact_versions=False):
        self.project_root = Path(__file__).parent
        self.environment_manager = EnvironmentManager(self.project_root)
        self.environment_report = self.environment_manager.prepare_environment(
            auto_install_missing=True,
            sync_exact_versions=sync_exact_versions,
            ensure_playwright_browser=True,
            ensure_config=True,
        )
        from common.config_manager import ConfigManager

        self.config = ConfigManager()
        self.allure_results_dir = self.project_root / self.config.get(
            "paths.allure_results", "allure-results"
        )
        self.allure_report_dir = self.project_root / self.config.get(
            "paths.allure_report", "allure-report"
        )
        self.tests_dir = self.project_root / self.config.get("paths.tests", "tests")
        self.record_raw_dir = self.project_root / self.config.get(
            "paths.record_raw", "record_raw"
        )
        self.run_history_path = self.project_root / "artifacts" / "run_history.json"
        self.latest_result = None

    def clean_results(self):
        if self.allure_results_dir.exists():
            shutil.rmtree(self.allure_results_dir)
        self.allure_results_dir.mkdir(parents=True, exist_ok=True)
        
        if self.allure_report_dir.exists():
            shutil.rmtree(self.allure_report_dir)

    def discover_tests(self):
        env = self._build_env(headless=False, browser=None, slow_mo=None)
        command = [
            sys.executable,
            "-m",
            "pytest",
            str(self.tests_dir),
            "--collect-only",
            "-q",
        ]

        result = subprocess.run(
            command,
            env=env,
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"failed to collect tests: {message}")

        test_cases = []
        for line in result.stdout.splitlines():
            nodeid = line.strip()
            if not nodeid or "::" not in nodeid:
                continue
            test_cases.append(self._build_test_case_metadata(nodeid))

        return test_cases

    def inspect_raw_script_contracts(self):
        parser = ScriptParser(self.record_raw_dir)
        parser.parse_all_scripts()
        return parser.get_framework_contract_report()

    def run_tests(
        self,
        test_path=None,
        headless=False,
        browser=None,
        slow_mo=None,
        selected_tests=None,
        return_details=False,
        continue_on_failure=False,
        progress_callback=None,
    ):
        pytest_started_at = datetime.now()
        pytest_started_perf = time.perf_counter()
        self.clean_results()
        
        test_files = list(self.tests_dir.glob("test_*.py"))
        if not test_files:
            print("No test files found in tests directory.")
            print("Please first transform recorded scripts (option 1) or add test files.")
            empty_result = {
                "success": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "No test files found in tests directory.",
                "targets": [],
                "command": [],
            }
            if return_details:
                return empty_result
            return False

        targets = self._normalize_targets(test_path=test_path, selected_tests=selected_tests)
        env = self._build_env(headless=headless, browser=browser, slow_mo=slow_mo)
        configured_browser = env["TEST_BROWSER"]

        print(f"Base URL: {env['BASE_URL']}")
        print(f"Browser: {configured_browser}")
        print(f"Headless: {env['TEST_HEADLESS']}")

        execution_mode = "batch"

        pytest_args = self._build_pytest_command(targets)
        print(f"Running tests with command: {' '.join(pytest_args)}")

        if progress_callback:
            progress_callback(
                {
                    "phase": "running",
                    "current_target": targets[0] if targets else None,
                    "current_index": 1,
                    "completed": 0,
                    "total": len(targets),
                    "per_test_results": [],
                }
            )

        try:
            details = self._run_pytest_command(
                pytest_args=pytest_args,
                env=env,
                configured_browser=configured_browser,
                targets=targets,
                execution_mode="batch",
                progress_callback=progress_callback,
            )
            details["pytest_started_at"] = pytest_started_at.isoformat(timespec="seconds")
            details["pytest_finished_at"] = datetime.now().isoformat(timespec="seconds")
            details["pytest_duration_seconds"] = round(time.perf_counter() - pytest_started_perf, 3)
            self.latest_result = details
            if progress_callback:
                progress_callback(
                    {
                        "phase": "tests-completed",
                        "current_target": None,
                        "current_index": len(targets),
                        "completed": len(targets),
                        "total": len(targets),
                        "per_test_results": details.get("per_test_results", []),
                    }
                )
            if return_details:
                return details
            return details["success"]
        except subprocess.TimeoutExpired:
            pytest_finished_at = datetime.now()
            print("Test execution timed out after 1 hour")
            timeout_result = {
                "success": False,
                "returncode": 124,
                "stdout": "",
                "stderr": "Test execution timed out after 1 hour",
                "targets": targets,
                "command": pytest_args,
                "commands": [pytest_args],
                "browser": configured_browser,
                "headless": env["TEST_HEADLESS"] == "true",
                "execution_mode": "batch",
                "per_test_results": [],
                "passed_count": 0,
                "failed_count": 1,
                "pytest_started_at": pytest_started_at.isoformat(timespec="seconds"),
                "pytest_finished_at": pytest_finished_at.isoformat(timespec="seconds"),
                "pytest_duration_seconds": round(time.perf_counter() - pytest_started_perf, 3),
            }
            self.latest_result = timeout_result
            if return_details:
                return timeout_result
            return False
        except Exception as e:
            pytest_finished_at = datetime.now()
            print(f"Error running tests: {e}")
            error_result = {
                "success": False,
                "returncode": 1,
                "stdout": "",
                "stderr": f"Error running tests: {e}",
                "targets": targets,
                "command": pytest_args,
                "commands": [pytest_args],
                "browser": configured_browser,
                "headless": env["TEST_HEADLESS"] == "true",
                "execution_mode": "batch",
                "per_test_results": [],
                "passed_count": 0,
                "failed_count": 1,
                "pytest_started_at": pytest_started_at.isoformat(timespec="seconds"),
                "pytest_finished_at": pytest_finished_at.isoformat(timespec="seconds"),
                "pytest_duration_seconds": round(time.perf_counter() - pytest_started_perf, 3),
            }
            self.latest_result = error_result
            if return_details:
                return error_result
            return False

    def _get_allure_command(self):
        allure_path = self.config.get("allure.path", "allure")
        if allure_path and allure_path != "allure":
            resolved = self._resolve_path(allure_path)
            if resolved.exists():
                return str(resolved)
        return self.environment_manager.detect_allure_command()

    def _resolve_path(self, value):
        path = Path(value)
        if path.is_absolute():
            return path
        return self.project_root / path

    def generate_report(self):
        print(f"\nGenerating Allure report...")

        if not self.config.get("allure.enabled", True):
            print("Allure HTML report is disabled; skipping optional report generation.")
            return False

        report_dir = self.allure_report_dir
        temp_report_dir = self.project_root / f"allure-report-temp-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.project_root / f"allure-report-backup-{timestamp}"

        if temp_report_dir.exists():
            shutil.rmtree(temp_report_dir, ignore_errors=True)

        allure_cmd = self._get_allure_command()
        if not allure_cmd:
            print("Allure command is not installed; skipping optional HTML report generation.")
            return False

        try:
            result = subprocess.run(
                [allure_cmd, "generate", str(self.allure_results_dir), "-o", str(temp_report_dir)],
                cwd=str(self.project_root),
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                if report_dir.exists():
                    shutil.move(str(report_dir), str(backup_dir))
                    print(f"Backed up previous report to: {backup_dir}")
                shutil.move(str(temp_report_dir), str(report_dir))
                print(f"Allure report generated successfully!")
                print(f"Report location: {report_dir}")
                return True
            else:
                print(f"Failed to generate Allure report:")
                print(result.stderr)
                if temp_report_dir.exists():
                    shutil.rmtree(temp_report_dir, ignore_errors=True)
                return False

        except FileNotFoundError:
            print(f"Error: Allure not found at {allure_cmd}")
            print("Please update the allure.path in config.yaml or add allure to system PATH")
            if temp_report_dir.exists():
                shutil.rmtree(temp_report_dir, ignore_errors=True)
            return False
        except Exception as e:
            print(f"Error generating report: {e}")
            if temp_report_dir.exists():
                shutil.rmtree(temp_report_dir, ignore_errors=True)
            return False

    def open_report(self):
        allure_cmd = self._get_allure_command()
        if not allure_cmd:
            print("Allure command is not installed; cannot open HTML report.")
            return False
        try:
            result = subprocess.run(
                [allure_cmd, "open", str(self.allure_report_dir)],
                cwd=str(self.project_root),
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print("Allure report opened in browser")
                return True
            else:
                print(f"Failed to open Allure report:")
                print(result.stderr)
                return False
                
        except FileNotFoundError:
            print("Error: 'allure' command not found.")
            return False

    def run_all(
        self,
        test_path=None,
        headless=False,
        open_report=True,
        browser=None,
        slow_mo=None,
        selected_tests=None,
        return_details=False,
        continue_on_failure=False,
        progress_callback=None,
    ):
        run_started_at = datetime.now()
        run_started_perf = time.perf_counter()
        print("=" * 60)
        print("UI AUTOMATION TEST FRAMEWORK - RUNNER")
        print("=" * 60)
        print_environment_report(self.environment_report)
        print("=" * 60)

        test_result = self.run_tests(
            test_path=test_path,
            headless=headless,
            browser=browser,
            slow_mo=slow_mo,
            selected_tests=selected_tests,
            return_details=True,
            continue_on_failure=continue_on_failure,
            progress_callback=progress_callback,
        )
        success = test_result["success"]

        if progress_callback:
            progress_callback(
                {
                    "phase": "reporting",
                    "current_target": None,
                    "current_index": len(test_result.get("targets", [])),
                    "completed": len(test_result.get("targets", [])),
                    "total": len(test_result.get("targets", [])),
                    "per_test_results": test_result.get("per_test_results", []),
                }
            )

        report_generated = self.generate_report()
        if report_generated:
            if open_report:
                self.open_report()

        combined_result = {
            **test_result,
            "report_generated": report_generated,
            "report_path": str(self.allure_report_dir),
            "started_at": run_started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(time.perf_counter() - run_started_perf, 3),
        }
        previous_result = self._find_previous_comparable_run(combined_result)
        if previous_result:
            previous_duration = previous_result.get("duration_seconds")
            if isinstance(previous_duration, (int, float)) and previous_duration >= 0:
                combined_result["previous_duration_seconds"] = round(float(previous_duration), 3)
                combined_result["duration_delta_seconds"] = round(
                    combined_result["duration_seconds"] - float(previous_duration),
                    3,
                )
                if previous_duration:
                    combined_result["duration_delta_percent"] = round(
                        (combined_result["duration_delta_seconds"] / float(previous_duration)) * 100,
                        2,
                    )
        self._append_run_history(combined_result)
        self.latest_result = combined_result
        if return_details:
            return combined_result
        return success

    def _run_history_items(self):
        if not self.run_history_path.exists():
            return []
        try:
            with open(self.run_history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            return []
        return []

    def _append_run_history(self, result):
        history = self._run_history_items()
        history.append(
            {
                "started_at": result.get("started_at", ""),
                "finished_at": result.get("finished_at", ""),
                "duration_seconds": result.get("duration_seconds"),
                "pytest_duration_seconds": result.get("pytest_duration_seconds"),
                "browser": result.get("browser", ""),
                "headless": result.get("headless", False),
                "targets": result.get("targets", []),
                "success": result.get("success", False),
                "returncode": result.get("returncode"),
                "passed_count": result.get("passed_count", 0),
                "failed_count": result.get("failed_count", 0),
            }
        )
        self.run_history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.run_history_path, "w", encoding="utf-8") as f:
            json.dump(history[-20:], f, ensure_ascii=False, indent=2)

    def _find_previous_comparable_run(self, current_result):
        current_targets = list(current_result.get("targets", []) or [])
        current_browser = current_result.get("browser")
        current_headless = current_result.get("headless")
        for item in reversed(self._run_history_items()):
            if item.get("targets", []) != current_targets:
                continue
            if item.get("browser") != current_browser:
                continue
            if item.get("headless") != current_headless:
                continue
            return item
        return None

    def _build_pytest_command(self, targets):
        return [
            sys.executable,
            "-m",
            "pytest",
            *targets,
            "-v",
            "--tb=short",
            "--alluredir",
            str(self.allure_results_dir),
            "--timeout=60",
            "--maxfail=0",
        ]

    def _run_pytest_command(
        self,
        pytest_args,
        env,
        configured_browser,
        targets,
        execution_mode,
        progress_callback=None,
    ):
        process = subprocess.Popen(
            pytest_args,
            env=env,
            cwd=str(self.project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        stdout_lines = []
        stderr_lines = []
        timed_out = {"value": False}

        def _read_pipe(stream, collector):
            try:
                for line in iter(stream.readline, ""):
                    collector.append(line)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        stderr_thread = threading.Thread(
            target=_read_pipe,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()

        def _kill_process_on_timeout():
            timed_out["value"] = True
            try:
                process.kill()
            except Exception:
                pass

        timeout_timer = threading.Timer(3600, _kill_process_on_timeout)
        timeout_timer.daemon = True
        timeout_timer.start()

        live_results = []
        seen_nodeids = set()

        try:
            for line in iter(process.stdout.readline, ""):
                stdout_lines.append(line)
                self._update_live_progress(
                    line=line,
                    requested_targets=targets,
                    command=pytest_args,
                    live_results=live_results,
                    seen_nodeids=seen_nodeids,
                    progress_callback=progress_callback,
                )
        finally:
            timeout_timer.cancel()
            try:
                process.stdout.close()
            except Exception:
                pass

        result_returncode = process.wait()
        stderr_thread.join(timeout=5)

        stdout_text = "".join(stdout_lines)
        stderr_text = "".join(stderr_lines)

        if timed_out["value"]:
            raise subprocess.TimeoutExpired(
                cmd=pytest_args,
                timeout=3600,
                output=stdout_text,
                stderr=stderr_text,
            )

        per_test_results, passed_count, failed_count = self._parse_pytest_results(
            stdout=stdout_text,
            requested_targets=targets,
            returncode=result_returncode,
            command=pytest_args,
        )

        details = {
            "success": result_returncode == 0,
            "returncode": result_returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "targets": targets,
            "command": pytest_args,
            "commands": [pytest_args],
            "browser": configured_browser,
            "headless": env["TEST_HEADLESS"] == "true",
            "execution_mode": execution_mode,
            "per_test_results": per_test_results,
            "passed_count": passed_count,
            "failed_count": failed_count,
        }

        print("\n" + "=" * 60)
        print("TEST RESULTS")
        print("=" * 60)
        print(stdout_text)

        if stderr_text:
            print("\nSTDERR:")
            print(stderr_text)

        print(f"\nReturn code: {result_returncode}")
        return details

    def _parse_pytest_status_line(self, line):
        match = self.PYTEST_STATUS_PATTERN.match((line or "").strip())
        if not match:
            return None, None
        return match.group(1), match.group(2)

    def _build_ordered_partial_results(self, requested_targets, parsed_results):
        parsed_map = {item["nodeid"]: item for item in parsed_results}
        return [parsed_map[target] for target in requested_targets if target in parsed_map]

    def _update_live_progress(
        self,
        line,
        requested_targets,
        command,
        live_results,
        seen_nodeids,
        progress_callback,
    ):
        if not progress_callback:
            return

        nodeid, matched_status = self._parse_pytest_status_line(line)
        if not nodeid or nodeid in seen_nodeids:
            return

        seen_nodeids.add(nodeid)
        live_results.append(
            {
                "index": len(live_results) + 1,
                "nodeid": nodeid,
                "success": matched_status in {"PASSED", "XFAIL"},
                "returncode": 0 if matched_status in {"PASSED", "XFAIL"} else 1,
                "stdout": line.strip(),
                "stderr": "",
                "command": command,
                "status": matched_status,
            }
        )

        ordered_results = self._build_ordered_partial_results(requested_targets, live_results)
        completed = len(ordered_results)
        next_target = next((target for target in requested_targets if target not in seen_nodeids), None)
        progress_callback(
            {
                "phase": "running" if next_target else "tests-completed",
                "current_target": next_target,
                "current_index": completed + 1 if next_target else len(requested_targets),
                "completed": completed,
                "total": len(requested_targets),
                "per_test_results": ordered_results,
            }
        )

    def _parse_pytest_results(self, stdout, requested_targets, returncode, command):
        parsed_results = []
        seen_nodeids = set()

        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            nodeid, matched_status = self._parse_pytest_status_line(line)
            if not nodeid or not matched_status:
                continue

            if nodeid in seen_nodeids:
                continue

            seen_nodeids.add(nodeid)
            parsed_results.append(
                {
                    "index": len(parsed_results) + 1,
                    "nodeid": nodeid,
                    "success": matched_status in {"PASSED", "XFAIL"},
                    "returncode": 0 if matched_status in {"PASSED", "XFAIL"} else 1,
                    "stdout": line,
                    "stderr": "",
                    "command": command,
                    "status": matched_status,
                }
            )

        if not parsed_results:
            overall_success = returncode == 0
            fallback_results = [
                {
                    "index": index,
                    "nodeid": target,
                    "success": overall_success,
                    "returncode": 0 if overall_success else 1,
                    "stdout": "",
                    "stderr": "",
                    "command": command,
                    "status": "PASSED" if overall_success else "FAILED",
                }
                for index, target in enumerate(requested_targets, start=1)
            ]
            passed_count = len(requested_targets) if overall_success else 0
            failed_count = 0 if overall_success else len(requested_targets)
            return fallback_results, passed_count, failed_count

        parsed_map = {item["nodeid"]: item for item in parsed_results}
        ordered_results = []
        for index, target in enumerate(requested_targets, start=1):
            item = parsed_map.get(target)
            if item is None:
                item = {
                    "index": index,
                    "nodeid": target,
                    "success": False,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "未在 pytest 输出中解析到该用例结果。",
                    "command": command,
                    "status": "UNKNOWN",
                }
            else:
                item = {**item, "index": index}
            ordered_results.append(item)

        passed_count = sum(1 for item in ordered_results if item["success"])
        failed_count = len(ordered_results) - passed_count
        return ordered_results, passed_count, failed_count

    def _run_tests_sequentially(self, targets, env, configured_browser, progress_callback=None):
        aggregate_stdout = []
        aggregate_stderr = []
        per_test_results = []
        commands = []
        passed_count = 0
        failed_count = 0

        print(f"Running selected tests sequentially in explicit order ({len(targets)} total).")

        for index, target in enumerate(targets, start=1):
            pytest_args = self._build_pytest_command([target])
            commands.append(pytest_args)

            print(f"\n[{index}/{len(targets)}] Running: {target}")
            print(f"Command: {' '.join(pytest_args)}")

            if progress_callback:
                progress_callback(
                    {
                        "phase": "running",
                        "current_target": target,
                        "current_index": index,
                        "completed": index - 1,
                        "total": len(targets),
                        "per_test_results": list(per_test_results),
                    }
                )

            try:
                result = subprocess.run(
                    pytest_args,
                    env=env,
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,
                )
                per_test_result = {
                    "index": index,
                    "nodeid": target,
                    "success": result.returncode == 0,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "command": pytest_args,
                }
            except subprocess.TimeoutExpired:
                per_test_result = {
                    "index": index,
                    "nodeid": target,
                    "success": False,
                    "returncode": 124,
                    "stdout": "",
                    "stderr": "Test execution timed out after 1 hour",
                    "command": pytest_args,
                }
            except Exception as exc:
                per_test_result = {
                    "index": index,
                    "nodeid": target,
                    "success": False,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"Error running test: {exc}",
                    "command": pytest_args,
                }

            per_test_results.append(per_test_result)
            if per_test_result["success"]:
                passed_count += 1
            else:
                failed_count += 1

            aggregate_stdout.append(
                f"===== [{index}/{len(targets)}] {target} =====\n{per_test_result['stdout'] or '无输出'}"
            )
            if per_test_result["stderr"]:
                aggregate_stderr.append(
                    f"===== [{index}/{len(targets)}] {target} =====\n{per_test_result['stderr']}"
                )

            if progress_callback:
                progress_callback(
                    {
                        "phase": "completed-target",
                        "current_target": target,
                        "current_index": index,
                        "completed": index,
                        "total": len(targets),
                        "per_test_results": list(per_test_results),
                    }
                )

        overall_success = failed_count == 0
        details = {
            "success": overall_success,
            "returncode": 0 if overall_success else 1,
            "stdout": "\n\n".join(aggregate_stdout),
            "stderr": "\n\n".join(aggregate_stderr),
            "targets": targets,
            "command": commands[0] if len(commands) == 1 else [],
            "commands": commands,
            "browser": configured_browser,
            "headless": env["TEST_HEADLESS"] == "true",
            "execution_mode": "sequential",
            "per_test_results": per_test_results,
            "passed_count": passed_count,
            "failed_count": failed_count,
        }

        print("\n" + "=" * 60)
        print("SEQUENTIAL TEST RESULTS")
        print("=" * 60)
        print(details["stdout"])
        if details["stderr"]:
            print("\nSTDERR:")
            print(details["stderr"])
        print(f"\nPassed: {passed_count}, Failed: {failed_count}")
        return details

    def _build_env(self, headless=False, browser=None, slow_mo=None):
        env = os.environ.copy()
        env["BASE_URL"] = self.config.get("base_url", "http://localhost:8080")
        env["PYTHONPATH"] = str(self.project_root)
        env["PYTHONIOENCODING"] = "utf-8"
        env["TEST_HEADLESS"] = "true" if headless else "false"
        env["TEST_BROWSER"] = browser or self.config.get("browser", "chromium")

        if slow_mo is not None:
            env["TEST_SLOW_MO"] = str(slow_mo)
        else:
            env.pop("TEST_SLOW_MO", None)

        return env

    def _normalize_targets(self, test_path=None, selected_tests=None):
        if selected_tests:
            if isinstance(selected_tests, str):
                selected_tests = [selected_tests]

            current_tests = []
            try:
                current_tests = [item["nodeid"] for item in self.discover_tests()]
            except Exception:
                current_tests = []

            normalized = []
            for target in selected_tests:
                if target in current_tests:
                    normalized.append(target)
                    continue

                prefix = target.split("[", 1)[0]
                matched = next((nodeid for nodeid in current_tests if nodeid == prefix or nodeid.startswith(prefix)), None)
                if matched:
                    normalized.append(matched)
                    continue

                normalized.append(target)

            return normalized
        if test_path:
            return [test_path]
        return [str(self.tests_dir)]

    def _build_test_case_metadata(self, nodeid):
        parts = nodeid.split("::")
        file_part = parts[0]
        class_name = parts[1] if len(parts) > 2 else ""
        function_name = parts[-1]
        case_name = Path(file_part).stem
        if case_name.startswith("test_"):
            case_name = case_name[5:]

        return {
            "nodeid": nodeid,
            "file": file_part,
            "class_name": class_name,
            "function_name": function_name,
            "case_name": case_name,
            "display_name": case_name,
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UI Automation Test Runner")
    parser.add_argument("--test", help="Path to specific test file or directory")
    parser.add_argument(
        "--nodeid",
        action="append",
        help="Specific pytest nodeid to run, can be provided multiple times",
    )
    parser.add_argument("--list-tests", action="store_true", help="List discovered test nodeids and exit")
    parser.add_argument("--headless", action="store_true", help="Run tests in headless mode")
    parser.add_argument("--browser", choices=["chromium", "firefox", "webkit"], help="Browser to use")
    parser.add_argument("--slow-mo", type=int, help="Delay operations in milliseconds")
    parser.add_argument("--no-open", action="store_true", help="Do not open report after generation")
    parser.add_argument(
        "--sync-requirements",
        action="store_true",
        help="Install exact versions from requirements.txt when local versions differ",
    )
    args = parser.parse_args()

    runner = TestRunner(sync_exact_versions=args.sync_requirements)
    if args.list_tests:
        for index, test_case in enumerate(runner.discover_tests(), start=1):
            print(f"{index}. {test_case['display_name']} -> {test_case['nodeid']}")
        sys.exit(0)

    success = runner.run_all(
        test_path=args.test,
        headless=args.headless,
        open_report=not args.no_open,
        browser=args.browser,
        slow_mo=args.slow_mo,
        selected_tests=args.nodeid,
    )
    
    sys.exit(0 if success else 1)
