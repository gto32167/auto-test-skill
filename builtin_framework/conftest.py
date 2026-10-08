import pytest
import allure
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright, Browser, BrowserContext, Page

from common.config_manager import ConfigManager
from common.raw_wrapper_support import (
    activate_execution_state,
    attach_execution_progress,
    capture_context_failure_artifacts,
    capture_page_artifacts,
    create_execution_state,
    deactivate_execution_state,
    note_execution_phase,
    note_execution_step,
    patch_raw_module_execution,
)
from common.runtime_config import (
    apply_runtime_config_to_raw_module,
    build_dashboard_url,
    get_runtime_settings,
    merge_runtime_parameters,
)
from common.url_utils import resolve_target_url


DEFAULT_LOGIN_URL = "https://passportnew-dev.xiaokeduo.com/#/login"
DEFAULT_USERNAME = "18674731640"
DEFAULT_PASSWORD = "123456"
DEFAULT_AUTH_STATE_PATH = Path(__file__).resolve().parent / "artifacts" / "auth" / "backend_storage_state.json"
STORE_ENTRY_SELECTOR = (
    "div:nth-child(7) > "
    ".antd-pro\\\\pages\\\\-index\\\\-index-store-info > "
    ".antd-pro\\\\pages\\\\-index\\\\-index-store-logo > img"
)


def _get_env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_env_int(name, default):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_login_value(config, env_name, config_key, default):
    env_value = os.getenv(env_name)
    if env_value:
        return env_value

    config_value = config.get(config_key, "")
    if config_value:
        return config_value

    return default


def _get_auth_storage_state_path(config):
    runtime = get_runtime_settings(config)
    configured_path = str(runtime.get("auth_storage_state_path") or "").strip()
    path = Path(configured_path) if configured_path else DEFAULT_AUTH_STATE_PATH
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


def _get_auth_bearer_token(config):
    runtime = get_runtime_settings(config)
    token = os.getenv("TEST_AUTHORIZATION_BEARER") or str(runtime.get("auth_bearer_token") or "").strip()
    return token


def _load_storage_state_path(config):
    auth_path = _get_auth_storage_state_path(config)
    if auth_path.exists():
        return str(auth_path)
    return None


def _save_storage_state(context, config):
    auth_path = _get_auth_storage_state_path(config)
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(auth_path))
    note_execution_step(
        f"共享登录：已保存登录态到 {auth_path}",
        function_name="_save_storage_state",
    )


def _wait_ready(page, pause_ms=1000):
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        expect(page.locator("body")).to_be_visible(timeout=15000)
    except Exception:
        pass


def _build_test_id_candidates(label):
    text = re.sub(r"\s+", " ", str(label or "").strip())
    if not text:
        return []

    compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "_", text).strip("_")
    candidates = []
    for candidate in (text, compact, compact.lower(), compact.replace("_", "-"), compact.lower().replace("_", "-")):
        candidate = str(candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _iter_click_locators(page, label, exact=True):
    for candidate in _build_test_id_candidates(label):
        yield page.get_by_test_id(candidate).first

    for role_name in ("button", "link", "menuitem", "tab", "option", "treeitem"):
        yield page.get_by_role(role_name, name=label, exact=exact).first

    yield page.get_by_text(label, exact=exact).first


def _smart_click_visible_text(page, label, exact=True, timeout=15000):
    last_error = None
    for locator in _iter_click_locators(page, label, exact=exact):
        try:
            expect(locator).to_be_visible(timeout=min(timeout, 3000))
            locator.scroll_into_view_if_needed(timeout=min(timeout, 3000))
            locator.click(timeout=timeout, force=True)
            _wait_ready(page, pause_ms=0)
            return
        except Exception as exc:
            last_error = exc
            continue
    raise AssertionError(f"未找到可点击文本: {label}") from last_error


def _smart_trigger_with_optional_new_page(page, trigger, pause_ms=1200):
    context = page.context
    before_pages = list(context.pages)
    trigger()

    def _new_pages():
        return [item for item in context.pages if item not in before_pages]

    for timeout in (min(500, pause_ms), max(500, pause_ms), max(1000, pause_ms)):
        new_pages = _new_pages()
        if new_pages:
            target_page = new_pages[-1]
            _wait_ready(target_page, pause_ms=0)
            return target_page
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:
            pass
        new_pages = _new_pages()
        if new_pages:
            target_page = new_pages[-1]
            _wait_ready(target_page, pause_ms=0)
            return target_page
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass

    _wait_ready(page, pause_ms=0)
    return page


def _is_store_backend_url(url, base_url, store_id):
    normalized_base = str(base_url or "").rstrip("/")
    return bool(normalized_base and store_id and str(url or "").startswith(f"{normalized_base}/#/{store_id}/"))


def _fill_backend_login_form(page, username, password):
    note_execution_step("共享登录：填写后台账号密码", function_name="_fill_backend_login_form")
    phone_input = page.get_by_placeholder("请输入注册时填写的手机号")
    phone_input.wait_for(state="visible", timeout=30000)
    phone_input.fill(username)
    password_input = page.get_by_placeholder("请输入密码")
    password_input.fill(password)
    # 登录页存在风控拦截，输入后需要稍长停留再触发登录。
    page.wait_for_timeout(5000)
    login_button = page.locator("button.ant-btn-primary").first
    expect(login_button).to_be_visible(timeout=10000)
    login_button.click(timeout=60000)
    _wait_for_post_login_redirect(page)


def _wait_for_post_login_redirect(page, timeout_ms=20000):
    deadline = time.time() + (timeout_ms / 1000)
    last_url = page.url
    while time.time() < deadline:
        _wait_ready(page, pause_ms=1000)
        last_url = page.url
        if "passportnew-dev" not in last_url:
            return last_url
        if "请输入注册时填写的手机号" not in page.locator("body").inner_text():
            return last_url
        if "登录" not in page.locator("body").inner_text():
            return last_url
    return last_url


def _perform_login(page, config):
    runtime = get_runtime_settings(config)
    login_url = os.getenv("TEST_LOGIN_URL") or runtime["login_url"] or DEFAULT_LOGIN_URL
    login_url = resolve_target_url(login_url, config)
    bearer_token = _get_auth_bearer_token(config)
    base_url = (
        runtime.get("base_url")
        or runtime.get("business_origin")
        or _origin_of(_get_runtime_entry_url(config))
        or ""
    )
    store_id = runtime.get("store_id", "")
    username = os.getenv("TEST_USERNAME") or runtime["backend_username"] or DEFAULT_USERNAME
    password = os.getenv("TEST_PASSWORD") or runtime["backend_password"] or DEFAULT_PASSWORD
    target_entry_url = _get_runtime_entry_url(config)
    bootstrap_url = (
        target_entry_url
        or resolve_target_url(
            build_dashboard_url(base_url, store_id),
            config,
        )
        or login_url
    )

    if bearer_token:
        with allure.step("Session bootstrap with bearer token"):
            note_execution_phase("setup", "检测到令牌，优先直接访问后台")
            note_execution_step("共享登录：使用令牌直接访问后台目标页", function_name="_perform_login")
            page.goto(bootstrap_url, wait_until="domcontentloaded")
            _wait_ready(page, pause_ms=5000)
            if _is_store_backend_url(page.url, base_url, store_id):
                return target_entry_url or page.url

    if _should_bypass_shared_login(config):
        with allure.step("Session bootstrap with configured test url"):
            note_execution_phase("setup", "共享登录已跳过，直接访问测试地址")
            note_execution_step("共享登录：直接访问配置的测试地址", function_name="_perform_login")
            page.goto(target_entry_url, wait_until="domcontentloaded")
            _wait_ready(page, pause_ms=1000)
            return page.url

    with allure.step("Session login"):
        note_execution_phase("setup", "开始执行共享登录")
        last_url = ""

        for attempt in range(4):
            note_execution_step(
                f"共享登录：访问后台目标页，第 {attempt + 1} 次尝试",
                function_name="_perform_login",
            )
            page.goto(bootstrap_url, wait_until="domcontentloaded")
            _wait_ready(page, pause_ms=4000 if attempt == 0 else 3000)
            last_url = page.url

            if _is_store_backend_url(page.url, base_url, store_id):
                note_execution_step("共享登录：已进入后台目标页面", function_name="_perform_login")
                break

            if "passportnew-dev" in page.url:
                note_execution_step("共享登录：命中登录页，准备提交登录表单", function_name="_perform_login")
                _fill_backend_login_form(page, username, password)
                last_url = page.url
                note_execution_step("共享登录：登录后重新访问后台目标页", function_name="_perform_login")
                page.goto(bootstrap_url, wait_until="domcontentloaded")
                _wait_ready(page, pause_ms=5000)
                last_url = page.url

            if _is_store_backend_url(page.url, base_url, store_id):
                note_execution_step("共享登录：登录后已进入后台目标页面", function_name="_perform_login")
                break

            if "mchcenter-dev.xiaokeduo.com" in page.url:
                note_execution_step("共享登录：落在商户中心，重新跳转后台目标页", function_name="_perform_login")
                page.goto(bootstrap_url, wait_until="domcontentloaded")
                _wait_ready(page, pause_ms=5000)
                last_url = page.url

                if _is_store_backend_url(page.url, base_url, store_id):
                    note_execution_step("共享登录：从商户中心跳回后台成功", function_name="_perform_login")
                    break
        else:
            raise AssertionError(f"共享登录后未进入目标后台页面: {last_url}")

        if target_entry_url and page.url != target_entry_url:
            note_execution_step("共享登录：进入统一配置的目标地址", function_name="_perform_login")
            page.goto(target_entry_url, wait_until="domcontentloaded")
            _wait_ready(page, pause_ms=3000)
        return target_entry_url or page.url


def _get_runtime_entry_url(config):
    runtime = get_runtime_settings(config)
    candidate = (
        runtime.get("test_url_input")
        or runtime.get("dashboard_url")
        or build_dashboard_url(runtime.get("business_origin", ""), runtime.get("store_id", ""))
    )
    return resolve_target_url(candidate, config)


def _origin_of(url):
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _should_bypass_shared_login(config):
    runtime = get_runtime_settings(config)
    target_origin = _origin_of(_get_runtime_entry_url(config))
    business_origin = _origin_of(runtime.get("business_origin", ""))
    default_origin = _origin_of(DEFAULT_LOGIN_URL).replace("passportnew-dev", "yunstore-dev")

    if not target_origin:
        return False
    if business_origin and target_origin != business_origin:
        return True
    if business_origin and business_origin != default_origin:
        return True
    return False


def _reset_session_page(page, context, login_state):
    for extra_page in list(context.pages):
        if extra_page != page:
            try:
                extra_page.close()
            except Exception:
                pass

    try:
        page.bring_to_front()
    except Exception:
        pass

    landing_url = login_state.get("landing_url")
    if landing_url and page.url != landing_url:
        note_execution_step("共享浏览器复位：返回上次登录落点页面", function_name="_reset_session_page")
        page.goto(landing_url)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)


def _extract_backend_login_context(login_args, login_kwargs, config):
    runtime = get_runtime_settings(config)
    base_url = str(
        login_kwargs.get("base_url")
        or runtime.get("base_url")
        or runtime.get("business_origin")
        or _origin_of(_get_runtime_entry_url(config))
        or ""
    ).rstrip("/")
    store_id = str(login_kwargs.get("store_id") or runtime.get("store_id") or "")
    username = (
        login_kwargs.get("phone")
        or login_kwargs.get("username")
        or runtime.get("backend_username")
        or DEFAULT_USERNAME
    )
    password = (
        login_kwargs.get("password")
        or runtime.get("backend_password")
        or DEFAULT_PASSWORD
    )

    if login_args:
        first_arg = login_args[0]
        if hasattr(first_arg, "base_url") or hasattr(first_arg, "store_id"):
            base_url = str(getattr(first_arg, "base_url", base_url) or base_url).rstrip("/")
            store_id = str(getattr(first_arg, "store_id", store_id) or store_id)
            username = (
                getattr(first_arg, "backend_phone", None)
                or getattr(first_arg, "phone", None)
                or getattr(first_arg, "username", None)
                or username
            )
            password = (
                getattr(first_arg, "backend_password", None)
                or getattr(first_arg, "password", None)
                or password
            )
        elif len(login_args) >= 5:
            base_url = str(login_args[1] or base_url).rstrip("/")
            store_id = str(login_args[2] or store_id)
            username = login_args[3] or username
            password = login_args[4] or password
        elif len(login_args) >= 4:
            base_url = str(login_args[0] or base_url).rstrip("/")
            store_id = str(login_args[1] or store_id)
            username = login_args[2] or username
            password = login_args[3] or password

    return base_url, store_id, username, password


def _ensure_backend_session_for_raw_script(page, config, *login_args, **login_kwargs):
    base_url, store_id, username, password = _extract_backend_login_context(login_args, login_kwargs, config)
    target_url = (
        _get_runtime_entry_url(config)
        or resolve_target_url(build_dashboard_url(base_url, store_id), config)
    )
    last_url = page.url

    if base_url and store_id and _is_store_backend_url(page.url, base_url, store_id):
        return

    for attempt in range(4):
        note_execution_step(
            f"raw 脚本共享登录校验：第 {attempt + 1} 次尝试",
            function_name="_ensure_backend_session_for_raw_script",
        )
        if target_url:
            page.goto(target_url, wait_until="domcontentloaded")
            _wait_ready(page, pause_ms=4000 if attempt == 0 else 3000)
            last_url = page.url

        if base_url and store_id and _is_store_backend_url(page.url, base_url, store_id):
            break

        if "passportnew-dev" in page.url:
            note_execution_step("raw 脚本共享登录校验：补做后台登录", function_name="_ensure_backend_session_for_raw_script")
            _fill_backend_login_form(page, username, password)
            last_url = page.url
            if target_url:
                page.goto(target_url, wait_until="domcontentloaded")
                _wait_ready(page, pause_ms=5000)
                last_url = page.url

        if base_url and store_id and _is_store_backend_url(page.url, base_url, store_id):
            break

        if "mchcenter-dev.xiaokeduo.com" in page.url and target_url:
            page.goto(target_url, wait_until="domcontentloaded")
            _wait_ready(page, pause_ms=5000)
            last_url = page.url
            if base_url and store_id and _is_store_backend_url(page.url, base_url, store_id):
                break
    else:
        raise AssertionError(f"共享登录后未进入目标后台页面: {last_url}")

def _safe_raw_screenshot(page, path):
    screenshot_path = Path(str(path))
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(
            path=str(screenshot_path),
            full_page=False,
            animations="disabled",
            timeout=2000,
        )
    except Exception as exc:
        print(f"Skip screenshot for {screenshot_path.name}: {exc}", file=sys.stderr)


def _patch_raw_module_for_framework(raw_module, config):
    raw_module = apply_runtime_config_to_raw_module(raw_module, config=config)
    raw_module = patch_raw_module_execution(raw_module)

    if getattr(raw_module, "_codex_framework_patched", False):
        return raw_module

    for name in ("save_shot", "safe_screenshot"):
        original = getattr(raw_module, name, None)
        if callable(original):
            def _patched_screenshot(page, path, *args, _original=original, **kwargs):
                try:
                    return _original(page, path, *args, **kwargs)
                except Exception:
                    return _safe_raw_screenshot(page, path)

            setattr(raw_module, name, _patched_screenshot)

    original_wait_ready = getattr(raw_module, "wait_ready", None)
    if callable(original_wait_ready):
        def _patched_wait_ready(page, *args, _original=original_wait_ready, **kwargs):
            try:
                return _wait_ready(page, pause_ms=0)
            except Exception:
                return _original(page, *args, **kwargs)

        setattr(raw_module, "wait_ready", _patched_wait_ready)

    original_click_visible_text = getattr(raw_module, "click_visible_text", None)
    if callable(original_click_visible_text):
        def _patched_click_visible_text(page, label, *args, _original=original_click_visible_text, **kwargs):
            try:
                return _smart_click_visible_text(page, label, *args, **kwargs)
            except Exception:
                return _original(page, label, *args, **kwargs)

        setattr(raw_module, "click_visible_text", _patched_click_visible_text)

    original_trigger_with_optional_new_page = getattr(raw_module, "trigger_with_optional_new_page", None)
    if callable(original_trigger_with_optional_new_page):
        def _patched_trigger_with_optional_new_page(page, trigger, *args, _original=original_trigger_with_optional_new_page, **kwargs):
            try:
                return _smart_trigger_with_optional_new_page(page, trigger, *args, **kwargs)
            except Exception:
                return _original(page, trigger, *args, **kwargs)

        setattr(raw_module, "trigger_with_optional_new_page", _patched_trigger_with_optional_new_page)

    original_capture_debug = getattr(raw_module, "capture_debug", None)
    if callable(original_capture_debug):
        def _patched_capture_debug(context, artifacts_dir, prefix):
            try:
                return original_capture_debug(context, artifacts_dir, prefix)
            except Exception:
                for index, debug_page in enumerate(list(getattr(context, "pages", [])), start=1):
                    _safe_raw_screenshot(debug_page, Path(str(artifacts_dir)) / f"{prefix}_page{index}.png")
                return None

        setattr(raw_module, "capture_debug", _patched_capture_debug)

    for login_name in ("login_backend", "login_to_store"):
        original_login = getattr(raw_module, login_name, None)
        if callable(original_login):
            def _patched_login(page, *args, _original=original_login, **kwargs):
                try:
                    return _ensure_backend_session_for_raw_script(page, config, *args, **kwargs)
                except Exception:
                    return _original(page, *args, **kwargs)

            setattr(raw_module, login_name, _patched_login)

    setattr(raw_module, "_codex_framework_patched", True)
    return raw_module


@pytest.fixture(scope="session")
def config():
    return ConfigManager()


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance, config):
    browser_type = os.getenv("TEST_BROWSER") or config.get("browser", "chromium")
    headless = _get_env_bool("TEST_HEADLESS", config.get("headless", False))
    slow_mo = _get_env_int("TEST_SLOW_MO", config.get("slow_mo", 0))
    keep_open = _get_env_bool("TEST_KEEP_BROWSER_OPEN", True)

    launch_options = {
        "headless": headless,
        "slow_mo": slow_mo,
    }

    if browser_type == "chromium":
        browser = playwright_instance.chromium.launch(**launch_options)
    elif browser_type == "firefox":
        browser = playwright_instance.firefox.launch(**launch_options)
    elif browser_type == "webkit":
        browser = playwright_instance.webkit.launch(**launch_options)
    else:
        browser = playwright_instance.chromium.launch(**launch_options)

    yield browser
    if not keep_open:
        browser.close()


@pytest.fixture(scope="session")
def context(browser, config):
    viewport = config.get("viewport", {"width": 1920, "height": 1080})
    storage_state_path = _load_storage_state_path(config)
    context_kwargs = {"viewport": viewport}
    if storage_state_path:
        context_kwargs["storage_state"] = storage_state_path
    bearer_token = _get_auth_bearer_token(config)
    if bearer_token:
        context_kwargs["extra_http_headers"] = {"Authorization": bearer_token}
    context = browser.new_context(**context_kwargs)
    if bearer_token:
        try:
            context.add_cookies(
                [
                    {
                        "name": "fat_token",
                        "value": bearer_token,
                        "domain": ".xiaokeduo.com",
                        "path": "/",
                        "httpOnly": False,
                        "secure": False,
                        "sameSite": "Lax",
                    }
                ]
            )
        except Exception:
            pass
    yield context
    if not _get_env_bool("TEST_KEEP_BROWSER_OPEN", True):
        context.close()


@pytest.fixture(scope="session")
def page(context, config):
    page = context.new_page()
    page.set_default_timeout(config.get("timeout", 30000))
    yield page
    if not _get_env_bool("TEST_KEEP_BROWSER_OPEN", True):
        page.close()


@pytest.fixture(scope="session")
def login_state():
    return {"logged_in": False, "landing_url": None}


@pytest.fixture(scope="function")
def session_login(page, context, config, login_state, request, raw_execution_state):
    if not login_state["logged_in"]:
        note_execution_step("共享浏览器会话：首次登录后台", function_name="session_login")
        login_state["landing_url"] = _perform_login(page, config)
        _save_storage_state(context, config)
        login_state["logged_in"] = True
    else:
        note_execution_step("共享浏览器会话：复用已登录会话", function_name="session_login")
        _reset_session_page(page, context, login_state)
    return page


@pytest.fixture(scope="function", autouse=True)
def raw_execution_state(request):
    state = create_execution_state(
        request.node.name,
        raw_wrapper=bool(request.node.get_closest_marker("raw_script_wrapper")),
    )
    request.node._codex_execution_state = state
    token = activate_execution_state(state)
    note_execution_phase("setup", "pytest 用例开始")
    try:
        yield state
    finally:
        note_execution_phase("teardown", "pytest 用例结束")
        deactivate_execution_state(token)


@pytest.fixture(scope="function", autouse=True)
def expose_runtime_handles(request, context, page):
    request.node._codex_context = context
    request.node._codex_page = page
    yield


@pytest.fixture(scope="function", autouse=True)
def raw_wrapper_runtime_overrides(request, config, monkeypatch):
    if not request.node.get_closest_marker("raw_script_wrapper"):
        yield
        return

    test_module = request.node.module
    cli_options = getattr(test_module, "CLI_OPTIONS", [])
    test_data = getattr(test_module, "TEST_DATA", None)
    if test_data is not None:
        monkeypatch.setattr(
            test_module,
            "TEST_DATA",
            merge_runtime_parameters(test_data, cli_options, config=config),
            raising=False,
        )

    original_loader = getattr(test_module, "_load_raw_module", None)
    if callable(original_loader):
        def _patched_load_raw_module():
            raw_module = original_loader()
            return _patch_raw_module_for_framework(raw_module, config)

        monkeypatch.setattr(test_module, "_load_raw_module", _patched_load_raw_module, raising=False)

    yield


@pytest.fixture(scope="function", autouse=True)
def screenshot_on_failure(request):
    yield
    rep = _get_failed_report(request.node)
    if not rep:
        return

    state = getattr(request.node, "_codex_execution_state", None)
    if state and state.get("failure_attached"):
        return
    note_execution_phase(f"pytest_{rep.when}", f"{rep.when} 阶段失败")
    if state and not state.get("progress_attached"):
        try:
            attach_execution_progress(state, name=f"execution_progress_{rep.when}")
        except Exception:
            pass

    try:
        if request.node.get_closest_marker("raw_script_wrapper"):
            context = request.getfixturevalue("context")
            capture_context_failure_artifacts(context, request.node.name)
            return

        page = request.getfixturevalue("page")
        capture_page_artifacts(
            page,
            request.node.name,
            page_label=f"main_{rep.when}",
            artifact_root="allure-results/page-artifacts",
        )
    except Exception:
        pass


@pytest.fixture(scope="function", autouse=True)
def page_source_on_failure(request):
    yield
    rep = getattr(request.node, "rep_call", None)
    if rep and rep.failed:
        return


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)
    if rep.failed:
        _attach_failure_artifacts(item, rep)


def _get_failed_report(node):
    for attr_name in ("rep_setup", "rep_call", "rep_teardown"):
        rep = getattr(node, attr_name, None)
        if rep and rep.failed:
            return rep
    return None


def _attach_failure_artifacts(item, rep):
    state = getattr(item, "_codex_execution_state", None)
    if state and not state.get("progress_attached"):
        try:
            note_execution_phase(f"pytest_{rep.when}", f"{rep.when} 阶段失败")
            attach_execution_progress(state, name=f"execution_progress_{rep.when}")
        except Exception:
            pass

    if state and state.get("failure_attached"):
        return

    try:
        context = getattr(item, "_codex_context", None)
        page = getattr(item, "_codex_page", None)

        if item.get_closest_marker("raw_script_wrapper") and context is not None:
            capture_context_failure_artifacts(context, item.name)
        elif page is not None:
            capture_page_artifacts(
                page,
                item.name,
                page_label=f"main_{rep.when}",
                artifact_root="allure-results/page-artifacts",
            )
        elif context is not None:
            capture_context_failure_artifacts(context, item.name)
        if state is not None:
            state["failure_attached"] = True
    except Exception:
        pass
