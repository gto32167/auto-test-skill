import io
import json
import os
import re
import sys
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from pathlib import Path

import allure


_EXECUTION_STATE = ContextVar("codex_execution_state", default=None)


class _TeeWriter(io.TextIOBase):
    def __init__(self, *streams):
        self._streams = [stream for stream in streams if stream is not None]

    def write(self, data):
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self._streams:
            if hasattr(stream, "flush"):
                stream.flush()


@contextmanager
def tee_std_streams():
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with redirect_stdout(_TeeWriter(sys.stdout, stdout_buffer)), redirect_stderr(
        _TeeWriter(sys.stderr, stderr_buffer)
    ):
        yield stdout_buffer, stderr_buffer


def create_execution_state(test_name, *, raw_wrapper=False):
    return {
        "test_name": str(test_name or ""),
        "raw_wrapper": bool(raw_wrapper),
        "current_phase": "",
        "current_step": "",
        "current_line": None,
        "current_function": "",
        "current_file": "",
        "current_url": "",
        "history": [],
        "progress_attached": False,
        "failure_attached": False,
    }


def activate_execution_state(state):
    return _EXECUTION_STATE.set(state)


def deactivate_execution_state(token):
    if token is None:
        return
    _EXECUTION_STATE.reset(token)


def get_active_execution_state():
    return _EXECUTION_STATE.get()


def note_execution_phase(phase, detail=""):
    state = get_active_execution_state()
    if not state:
        return

    phase_text = str(phase or "").strip()
    detail_text = str(detail or "").strip()
    state["current_phase"] = phase_text
    if detail_text:
        _append_history_entry(
            state,
            kind="phase",
            label=f"{phase_text}: {detail_text}" if phase_text else detail_text,
        )


def note_execution_step(step, *, line_no=None, function_name="", file_path="", allow_duplicate=False):
    state = get_active_execution_state()
    if not state:
        return

    label = str(step or "").strip()
    if not label:
        return

    normalized = _normalize_history_identity(
        kind="step",
        label=label,
        line_no=line_no,
        function_name=function_name,
        file_path=file_path,
    )
    if not allow_duplicate and normalized == state.get("_last_history_identity"):
        _update_execution_location(
            state,
            line_no=line_no,
            function_name=function_name,
            file_path=file_path,
        )
        return

    state["current_step"] = label
    _update_execution_location(
        state,
        line_no=line_no,
        function_name=function_name,
        file_path=file_path,
    )
    _append_history_entry(
        state,
        kind="step",
        label=label,
        line_no=line_no,
        function_name=function_name,
        file_path=file_path,
    )
    state["_last_history_identity"] = normalized


def update_execution_location(*, line_no=None, function_name="", file_path=""):
    state = get_active_execution_state()
    if not state:
        return
    _update_execution_location(
        state,
        line_no=line_no,
        function_name=function_name,
        file_path=file_path,
    )


def note_page_state(page, page_label="page"):
    state = get_active_execution_state()
    if not state:
        return
    try:
        state["current_url"] = page.url
    except Exception:
        return
    if page_label:
        state["last_page_label"] = page_label


def build_execution_progress_snapshot(state=None):
    state = state or get_active_execution_state()
    if not state:
        return {}

    history = list(state.get("history", []) or [])
    current_step = state.get("current_step", "").strip()
    current_phase = state.get("current_phase", "").strip()
    current_file = state.get("current_file", "").strip()
    current_function = state.get("current_function", "").strip()
    current_line = state.get("current_line")
    current_url = state.get("current_url", "").strip()

    snapshot = {
        "test_name": state.get("test_name", ""),
        "raw_wrapper": bool(state.get("raw_wrapper")),
        "current_phase": current_phase,
        "current_step": current_step or _build_location_label(
            file_path=current_file,
            function_name=current_function,
            line_no=current_line,
        ),
        "current_file": current_file,
        "current_function": current_function,
        "current_line": current_line,
        "current_url": current_url,
        "history": history,
    }
    return snapshot


def attach_execution_progress(state=None, *, name="execution_progress"):
    state = state or get_active_execution_state()
    if not state:
        return {}

    snapshot = build_execution_progress_snapshot(state)
    allure.attach(
        format_execution_progress(snapshot),
        name=name,
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        name=f"{name}_json",
        attachment_type=allure.attachment_type.JSON,
    )
    state["progress_attached"] = True
    return snapshot


def format_execution_progress(snapshot):
    if not snapshot:
        return "No execution progress available."

    lines = [
        f"current_phase: {snapshot.get('current_phase') or '<unknown>'}",
        f"current_step: {snapshot.get('current_step') or '<unknown>'}",
    ]

    location = _build_location_label(
        file_path=snapshot.get("current_file", ""),
        function_name=snapshot.get("current_function", ""),
        line_no=snapshot.get("current_line"),
    )
    if location:
        lines.append(f"current_location: {location}")
    if snapshot.get("current_url"):
        lines.append(f"current_url: {snapshot['current_url']}")

    history = snapshot.get("history", []) or []
    if history:
        lines.append("recent_history:")
        for entry in history[-12:]:
            lines.append(f"  - {entry.get('label', '')}{_format_history_suffix(entry)}")

    return "\n".join(lines)


def patch_raw_module_execution(raw_module):
    if getattr(raw_module, "_codex_execution_tracing_patched", False):
        return raw_module

    original_main = getattr(raw_module, "main", None)
    raw_file = Path(str(getattr(raw_module, "__file__", "") or "")).resolve()
    if not callable(original_main) or not raw_file.exists():
        setattr(raw_module, "_codex_execution_tracing_patched", True)
        return raw_module

    step_lookup = _build_step_lookup(raw_file)
    tracked_path = os.path.normcase(str(raw_file))

    def _trace(frame, event, arg):
        if os.path.normcase(str(Path(frame.f_code.co_filename).resolve())) != tracked_path:
            return _trace

        function_name = frame.f_code.co_name
        line_no = frame.f_lineno

        if event == "call" and function_name not in {"<module>"}:
            update_execution_location(
                line_no=line_no,
                function_name=function_name,
                file_path=str(raw_file),
            )
            return _trace

        if event == "line":
            update_execution_location(
                line_no=line_no,
                function_name=function_name,
                file_path=str(raw_file),
            )
            step_label = step_lookup.get(line_no)
            if step_label:
                note_execution_step(
                    step_label,
                    line_no=line_no,
                    function_name=function_name,
                    file_path=str(raw_file),
                )
        return _trace

    def _wrapped_main(*args, **kwargs):
        note_execution_phase("raw_main", f"开始执行 {raw_file.name}.main()")
        previous_trace = sys.gettrace()
        sys.settrace(_trace)
        try:
            return original_main(*args, **kwargs)
        finally:
            sys.settrace(previous_trace)
            note_execution_phase("raw_main", f"结束执行 {raw_file.name}.main()")

    setattr(raw_module, "main", _wrapped_main)
    setattr(raw_module, "_codex_execution_tracing_patched", True)
    return raw_module


def build_raw_failure_report(
    *,
    result=None,
    stdout_text="",
    stderr_text="",
    exception=None,
    traceback_text="",
):
    execution_snapshot = build_execution_progress_snapshot()
    exception_text = str(exception) if exception else ""
    combined_text = "\n".join(
        text
        for text in (stderr_text, exception_text, traceback_text, stdout_text)
        if str(text or "").strip()
    )
    source_message = _extract_primary_message(
        stderr_text=stderr_text,
        exception_text=exception_text,
        traceback_text=traceback_text,
        stdout_text=stdout_text,
    )
    reason = _clean_message(source_message or exception_text or combined_text or "raw script execution failed")
    failure_type = _classify_failure_type(combined_text or reason)
    stage = _infer_failure_stage(source_message or exception_text or combined_text or reason)
    hint = _build_failure_hint(failure_type, stage)

    return {
        "failure_type": failure_type,
        "stage": stage,
        "reason": reason,
        "source_message": source_message or reason,
        "hint": hint,
        "current_phase": execution_snapshot.get("current_phase", ""),
        "current_step": execution_snapshot.get("current_step", ""),
        "current_file": execution_snapshot.get("current_file", ""),
        "current_function": execution_snapshot.get("current_function", ""),
        "current_line": execution_snapshot.get("current_line"),
        "current_url": execution_snapshot.get("current_url", ""),
        "history": execution_snapshot.get("history", []),
        "result": result,
        "exception_type": type(exception).__name__ if exception else "",
        "stdout_text": stdout_text or "",
        "stderr_text": stderr_text or "",
        "traceback_text": traceback_text or "",
    }


def format_raw_failure_report(report):
    lines = [
        f"RAW_SCRIPT_FAILURE [{report.get('failure_type', 'BUSINESS_FAILURE')}]",
        f"stage: {report.get('stage', 'unknown')}",
        f"reason: {report.get('reason', 'unknown error')}",
        f"hint: {report.get('hint', '')}",
    ]
    if report.get("current_phase"):
        lines.append(f"current_phase: {report['current_phase']}")
    if report.get("current_step"):
        lines.append(f"current_step: {report['current_step']}")
    location = _build_location_label(
        file_path=report.get("current_file", ""),
        function_name=report.get("current_function", ""),
        line_no=report.get("current_line"),
    )
    if location:
        lines.append(f"current_location: {location}")
    if report.get("current_url"):
        lines.append(f"current_url: {report['current_url']}")
    if report.get("result") not in (None, ""):
        lines.append(f"raw_result: {report['result']}")
    if report.get("exception_type"):
        lines.append(f"exception_type: {report['exception_type']}")
    return "\n".join(lines)


def attach_raw_failure_report(report, *, stdout_text="", stderr_text="", traceback_text=""):
    allure.attach(
        format_raw_failure_report(report),
        name="raw_failure_summary",
        attachment_type=allure.attachment_type.TEXT,
    )
    allure.attach(
        json.dumps(
            {
                "failure_type": report.get("failure_type"),
                "stage": report.get("stage"),
                "reason": report.get("reason"),
                "hint": report.get("hint"),
                "current_phase": report.get("current_phase"),
                "current_step": report.get("current_step"),
                "current_file": report.get("current_file"),
                "current_function": report.get("current_function"),
                "current_line": report.get("current_line"),
                "current_url": report.get("current_url"),
                "history": report.get("history", []),
                "result": report.get("result"),
                "exception_type": report.get("exception_type"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        name="raw_failure_report",
        attachment_type=allure.attachment_type.JSON,
    )
    attach_execution_progress(name="raw_execution_progress")
    if stdout_text.strip():
        allure.attach(stdout_text, name="raw_stdout", attachment_type=allure.attachment_type.TEXT)
    if stderr_text.strip():
        allure.attach(stderr_text, name="raw_stderr", attachment_type=allure.attachment_type.TEXT)
    if traceback_text.strip():
        allure.attach(traceback_text, name="raw_traceback", attachment_type=allure.attachment_type.TEXT)


def capture_page_artifacts(page, test_name, *, page_label="page", artifact_root=None):
    artifact_root = Path(artifact_root or "allure-results/runtime-artifacts")
    target_dir = artifact_root / _sanitize_name(test_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    attachments = {
        "page_label": page_label,
        "url": "",
        "screenshot_path": "",
        "html_path": "",
        "errors": [],
    }

    try:
        attachments["url"] = page.url
        note_page_state(page, page_label)
    except Exception as exc:
        attachments["errors"].append(f"url: {exc}")

    allure.attach(
        attachments["url"] or "<unavailable>",
        name=f"{page_label}_url",
        attachment_type=allure.attachment_type.TEXT,
    )

    screenshot_path = target_dir / f"{page_label}.png"
    screenshot_error = _save_page_screenshot(page, screenshot_path)
    if screenshot_error:
        attachments["errors"].append(f"screenshot: {screenshot_error}")
        allure.attach(
            f"Skip screenshot for {page_label}: {screenshot_error}",
            name=f"{page_label}_screenshot_error",
            attachment_type=allure.attachment_type.TEXT,
        )
    else:
        attachments["screenshot_path"] = str(screenshot_path)
        allure.attach.file(
            str(screenshot_path),
            name=f"{page_label}_screenshot",
            attachment_type=allure.attachment_type.PNG,
        )

    html_path = target_dir / f"{page_label}.html"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        attachments["html_path"] = str(html_path)
        allure.attach.file(
            str(html_path),
            name=f"{page_label}_html",
            attachment_type=allure.attachment_type.HTML,
        )
    except Exception as exc:
        attachments["errors"].append(f"html: {exc}")
        allure.attach(
            f"Skip html capture for {page_label}: {exc}",
            name=f"{page_label}_html_error",
            attachment_type=allure.attachment_type.TEXT,
        )

    return attachments


def capture_context_failure_artifacts(context, test_name):
    pages = list(getattr(context, "pages", []) or [])
    if not pages:
        allure.attach(
            "No open pages available for failure diagnostics.",
            name="raw_failure_page_inventory",
            attachment_type=allure.attachment_type.TEXT,
        )
        return []

    artifacts = []
    for index, page in enumerate(pages, start=1):
        artifacts.append(
            capture_page_artifacts(
                page,
                test_name,
                page_label=f"page{index}",
                artifact_root="allure-results/raw-wrapper-artifacts",
            )
        )

    allure.attach(
        json.dumps(artifacts, ensure_ascii=False, indent=2),
        name="raw_failure_page_inventory",
        attachment_type=allure.attachment_type.JSON,
    )
    return artifacts


def _extract_primary_message(*, stderr_text, exception_text, traceback_text, stdout_text):
    exception_message = _first_relevant_line(exception_text)
    if exception_message:
        return exception_message

    traceback_message = _extract_traceback_message(traceback_text)
    if traceback_message:
        return traceback_message

    for text in (stderr_text, stdout_text):
        message = _last_relevant_line(text)
        if message:
            return message
    return ""


def _first_relevant_line(text):
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("call log:"):
            continue
        if line.startswith("Traceback (most recent call last):"):
            continue
        return line
    return ""


def _last_relevant_line(text):
    relevant_lines = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("call log:"):
            continue
        if line.startswith("Traceback (most recent call last):"):
            continue
        relevant_lines.append(line)
    return relevant_lines[-1] if relevant_lines else ""


def _extract_traceback_message(text):
    lines = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Traceback (most recent call last):"):
            continue
        if line.startswith('File "') or line.startswith("File '"):
            continue
        lines.append(line)
    return lines[-1] if lines else ""


def _clean_message(text):
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^[^:]{0,120}failed:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[^:]{0,120}失败[:：]\s*", "", cleaned)
    cleaned = re.sub(r"^AssertionError:\s*", "", cleaned)
    return cleaned.strip() or "raw script execution failed"


def _classify_failure_type(text):
    lowered = str(text or "").lower()

    if any(
        token in lowered
        for token in (
            "raw script not found",
            "未配置",
            "configuration",
            "config.yaml",
            "缺少 `",
            "invalidoperation",
            "conversionsyntax",
            "argument --",
            "unrecognized arguments",
            "invalid int value",
            "invalid choice",
            "usage:",
        )
    ):
        return "CONFIG_FAILURE"
    if any(
        token in lowered
        for token in (
            "asyncio loop",
            "raw_script_wrapper",
            "_build_playwright_proxy",
            "conftest.py",
            "fixture",
            "shared login",
            "共享登录后未进入目标后台页面",
            "page.screenshot",
            "screenshot:",
        )
    ):
        return "FRAMEWORK_FAILURE"
    if any(
        token in lowered
        for token in (
            "page.goto: timeout",
            "navigation timeout",
            "net::",
            "err_",
            "econn",
            "etimedout",
            "dns",
            "name_not_resolved",
            "connection refused",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway timeout",
            "targetclosederror",
            "target page, context or browser has been closed",
            "browser has been closed",
        )
    ):
        return "ENVIRONMENT_FAILURE"
    return "BUSINESS_FAILURE"


def _infer_failure_stage(text):
    text = str(text or "")
    stage_patterns = (
        (r"登录|login|passport|手机号|请输入密码", "login"),
        (r"商品列表|出售中|编辑商品|库存|运费|推广|商品管理", "goods_setup"),
        (r"后台订单未显示已完成|后台订单|所有订单|订单状态", "receipt"),
        (r"支付|payid|余额支付|立即支付|订单支付成功|yepayorder", "payment"),
        (r"确认收货|待收货|交易完成|已完成|订单详情|完成订单|提货点|我已取到货物|待自提|待提货", "receipt"),
        (r"待发包裹|发货|配送员|无需物流|快递订单|同城订单|自提订单", "fulfillment"),
        (r"立即购买|规格弹层|xkdSku|确认订单页|待支付|提交订单|快递发货|同城配送|上门自提", "order_create"),
        (r"screenshot|截图|html|page source", "diagnostics"),
    )
    for pattern, stage in stage_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return stage
    return "unknown"


def _build_failure_hint(failure_type, stage):
    base_hints = {
        "BUSINESS_FAILURE": "更可能是页面流程、元素状态或业务断言未满足。优先检查步骤逻辑、选择器和页面状态。",
        "ENVIRONMENT_FAILURE": "更可能是环境、网络、站点可用性或浏览器会话异常。优先检查环境连通性、服务状态和重试结果。",
        "FRAMEWORK_FAILURE": "更可能是框架包装、共享登录、转换或执行链路异常。优先检查 conftest、wrapper 和转换结果。",
        "CONFIG_FAILURE": "更可能是统一环境配置或脚本默认参数不匹配。优先检查测试地址、账号、store_id、H5 手机号等配置。",
    }
    stage_suffix = {
        "login": " 当前阶段在登录或会话建立。",
        "goods_setup": " 当前阶段在后台选品或商品编辑。",
        "order_create": " 当前阶段在 H5 下单、规格确认或配送方式选择。",
        "payment": " 当前阶段在支付请求或支付结果校验。",
        "fulfillment": " 当前阶段在后台发货或订单履约。",
        "receipt": " 当前阶段在确认收货或终态校验。",
        "diagnostics": " 当前阶段在失败取证。",
        "unknown": "",
    }
    return base_hints.get(failure_type, base_hints["BUSINESS_FAILURE"]) + stage_suffix.get(stage, "")


def _save_page_screenshot(page, target_path):
    attempts = (
        {"full_page": False, "timeout": 2000},
        {"full_page": False, "timeout": 1000},
        {"full_page": True, "timeout": 2000},
    )
    last_error = None
    for options in attempts:
        try:
            page.screenshot(
                path=str(target_path),
                animations="disabled",
                **options,
            )
            return None
        except Exception as exc:
            last_error = exc
    return str(last_error) if last_error else "unknown screenshot error"


def _sanitize_name(value):
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("_") or "artifact"


def _append_history_entry(state, *, kind, label, line_no=None, function_name="", file_path=""):
    history = state.setdefault("history", [])
    history.append(
        {
            "index": len(history) + 1,
            "timestamp": round(time.time(), 3),
            "kind": kind,
            "label": str(label or "").strip(),
            "line_no": line_no,
            "function_name": str(function_name or "").strip(),
            "file_path": str(file_path or "").strip(),
        }
    )
    if len(history) > 100:
        del history[:-100]


def _update_execution_location(state, *, line_no=None, function_name="", file_path=""):
    if line_no:
        state["current_line"] = int(line_no)
    if function_name:
        state["current_function"] = str(function_name).strip()
    if file_path:
        state["current_file"] = str(file_path).strip()


def _normalize_history_identity(*, kind, label, line_no=None, function_name="", file_path=""):
    return (
        str(kind or "").strip(),
        str(label or "").strip(),
        int(line_no) if line_no else None,
        str(function_name or "").strip(),
        str(file_path or "").strip(),
    )


def _format_history_suffix(entry):
    location = _build_location_label(
        file_path=entry.get("file_path", ""),
        function_name=entry.get("function_name", ""),
        line_no=entry.get("line_no"),
    )
    return f" [{location}]" if location else ""


def _build_location_label(*, file_path="", function_name="", line_no=None):
    parts = []
    if file_path:
        parts.append(Path(str(file_path)).name)
    if function_name:
        parts.append(f"{function_name}()")
    if line_no:
        parts.append(f"line {line_no}")
    return " / ".join(parts)


def _build_step_lookup(raw_file):
    try:
        lines = Path(raw_file).read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}

    step_lookup = {}
    active_step = ""
    for line_no, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        step_match = re.match(r"#\s*(步骤[^#]+)", stripped)
        if step_match:
            active_step = step_match.group(1).strip()
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if active_step:
            step_lookup[line_no] = active_step
    return step_lookup
