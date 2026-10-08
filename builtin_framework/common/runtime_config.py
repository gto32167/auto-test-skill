import re
from copy import deepcopy
from urllib.parse import urlsplit

from common.config_manager import ConfigManager


DEFAULT_PASSPORT_ORIGIN = ""
DEFAULT_BUSINESS_ORIGIN = ""


def parse_h5_account_entries(raw_value, default_pay_password="", default_verification_code=""):
    entries_by_phone = {}
    ordered_phones = []

    if isinstance(raw_value, list):
        items = raw_value
    else:
        items = str(raw_value or "").splitlines()

    for item in items:
        if isinstance(item, dict):
            phone = str(item.get("phone") or "").strip()
            pay_password = str(item.get("pay_password") or "").strip()
            verification_code = str(item.get("verification_code") or "").strip()
        else:
            text = str(item or "").strip()
            if not text:
                continue
            parts = [part.strip() for part in re.split(r"[，,]", text)]
            phone = parts[0] if parts else ""
            pay_password = parts[1] if len(parts) > 1 else ""
            verification_code = parts[2] if len(parts) > 2 else ""

        if not phone:
            continue

        if phone not in entries_by_phone:
            ordered_phones.append(phone)

        entries_by_phone[phone] = {
            "phone": phone,
            "pay_password": pay_password or str(default_pay_password or "").strip(),
            "verification_code": verification_code or str(default_verification_code or "").strip(),
        }

    return [entries_by_phone[phone] for phone in ordered_phones]


def format_h5_account_entries(entries):
    formatted_lines = []
    for entry in parse_h5_account_entries(entries):
        formatted_lines.append(
            f"{entry['phone']}, {entry['pay_password']}, {entry['verification_code']}"
        )
    return "\n".join(formatted_lines)


def parse_phone_entries(raw_value):
    if isinstance(raw_value, list) and any(isinstance(item, dict) for item in raw_value):
        return [entry["phone"] for entry in parse_h5_account_entries(raw_value)]

    if isinstance(raw_value, list):
        items = raw_value
    else:
        text = str(raw_value or "")
        items = re.split(r"[\r\n,;]+", text)

    seen = set()
    phones = []
    for item in items:
        phone = str(item or "").strip()
        if not phone or phone in seen:
            continue
        seen.add(phone)
        phones.append(phone)
    return phones


def format_phone_entries(phones):
    return "\n".join(parse_phone_entries(phones))


def normalize_test_url_input(value):
    text = str(value or "").strip()
    if not text:
        return ""

    split_result = urlsplit(text)
    if split_result.scheme and split_result.netloc and not split_result.path and not split_result.query and not split_result.fragment:
        return f"{text.rstrip('/')}/"
    return text


def _resolve_config(config=None):
    return config or ConfigManager()


def _safe_get(config, key, default=""):
    value = config.get(key, default)
    return default if value is None else value


def extract_origin_and_store_id(target_url):
    text = str(target_url or "").strip()
    if not text:
        return "", ""

    split_result = urlsplit(text)
    if not split_result.scheme or not split_result.netloc:
        return "", ""

    origin = f"{split_result.scheme}://{split_result.netloc}"
    fragment = (split_result.fragment or "").lstrip("/")
    match = re.match(r"(?P<store_id>[^/?#]+)/", fragment)
    if match:
        return origin, match.group("store_id")

    return origin, ""


def _looks_like_generated_dashboard_url(raw_dashboard_url, business_origin, store_id):
    if not raw_dashboard_url or not business_origin or not store_id:
        return False
    return raw_dashboard_url == build_dashboard_url(business_origin, store_id)


def _is_dashboard_route(url):
    text = str(url or "").strip()
    if not text:
        return False
    fragment = (urlsplit(text).fragment or "").lstrip("/")
    return fragment.endswith("/dashboard")


def get_runtime_settings(config=None):
    config = _resolve_config(config)
    runtime = config.config.get("runtime", {}) or {}
    url_mappings = config.config.get("url_mappings", {}) or {}

    raw_test_url_input = normalize_test_url_input(runtime.get("test_url_input", ""))
    raw_dashboard_url = str(runtime.get("dashboard_url") or "").strip()
    raw_goods_list_url = str(runtime.get("goods_list_url") or "").strip()
    input_or_dashboard_url = raw_test_url_input or raw_dashboard_url

    dashboard_origin, dashboard_store_id = extract_origin_and_store_id(input_or_dashboard_url)
    goods_origin, goods_store_id = extract_origin_and_store_id(raw_goods_list_url)

    business_origin = (
        str(runtime.get("business_origin") or "").strip()
        or goods_origin
        or dashboard_origin
        or url_mappings.get(DEFAULT_BUSINESS_ORIGIN, "")
        or _safe_get(config, "base_url", "")
    )
    store_id = (
        str(runtime.get("store_id") or "").strip()
        or goods_store_id
        or dashboard_store_id
    )

    passport_origin = (
        str(runtime.get("passport_origin") or "").strip()
        or url_mappings.get(DEFAULT_PASSPORT_ORIGIN, "")
        or DEFAULT_PASSPORT_ORIGIN
    )
    login_url = (
        str(runtime.get("login_url") or "").strip()
        or _safe_get(config, "login.url", "")
        or f"{passport_origin}/#/login"
    )

    backend_username = (
        str(runtime.get("backend_username") or "").strip()
        or _safe_get(config, "env.username", "")
    )
    backend_password = (
        str(runtime.get("backend_password") or "").strip()
        or _safe_get(config, "env.password", "")
    )
    auth_storage_state_path = str(runtime.get("auth_storage_state_path") or "").strip()

    raw_h5_accounts = runtime.get("h5_accounts", [])
    legacy_pay_password = str(runtime.get("pay_password") or "").strip()
    legacy_verification_code = str(runtime.get("h5_verification_code") or "").strip()
    if raw_h5_accounts:
        h5_accounts = parse_h5_account_entries(raw_h5_accounts)
    else:
        h5_accounts = parse_h5_account_entries(
            runtime.get("h5_phones", []),
            default_pay_password=legacy_pay_password,
            default_verification_code=legacy_verification_code,
        )

    h5_phones = [entry["phone"] for entry in h5_accounts]
    configured_default_phone = str(runtime.get("h5_default_phone") or "").strip()
    default_h5_account = next(
        (entry for entry in h5_accounts if entry["phone"] == configured_default_phone),
        h5_accounts[0] if h5_accounts else None,
    )
    h5_default_phone = (
        configured_default_phone
        or (h5_phones[0] if h5_phones else "")
        or backend_username
    )

    dashboard_url = raw_test_url_input or raw_dashboard_url
    if not raw_test_url_input and _looks_like_generated_dashboard_url(raw_dashboard_url, business_origin, store_id):
        dashboard_url = normalize_test_url_input(business_origin)
    if not dashboard_url and business_origin:
        dashboard_url = build_dashboard_url(business_origin, store_id)

    goods_list_url = raw_goods_list_url
    if business_origin and store_id:
        goods_list_url = build_goods_list_url(business_origin, store_id)
    elif not goods_list_url and store_id:
        goods_list_url = build_goods_list_url(_safe_get(config, "base_url", ""), store_id)

    return {
        "test_url_input": dashboard_url,
        "dashboard_url": dashboard_url,
        "goods_list_url": goods_list_url,
        "login_url": login_url,
        "auth_storage_state_path": auth_storage_state_path,
        "business_origin": business_origin,
        "passport_origin": passport_origin,
        "base_url": business_origin,
        "store_id": store_id,
        "backend_username": backend_username,
        "backend_password": backend_password,
        "h5_accounts": h5_accounts,
        "h5_phones": h5_phones,
        "h5_default_phone": h5_default_phone,
        "h5_verification_code": (
            str((default_h5_account or {}).get("verification_code") or "").strip()
            or legacy_verification_code
        ),
        "pay_password": (
            str((default_h5_account or {}).get("pay_password") or "").strip()
            or legacy_pay_password
        ),
    }


def build_goods_list_url(origin, store_id):
    origin = str(origin or "").strip()
    store_id = str(store_id or "").strip()
    if not origin or not store_id:
        return ""
    return f"{origin}/#/{store_id}/goods/onsale"


def build_dashboard_url(origin, store_id):
    origin = str(origin or "").strip()
    store_id = str(store_id or "").strip()
    if not origin:
        return ""
    if not store_id:
        return origin
    return f"{origin}/#/{store_id}/dashboard"


def get_runtime_override_map(config=None):
    settings = get_runtime_settings(config)
    override_map = {
        "base_url": settings["base_url"],
        "store_id": settings["store_id"],
        "phone": settings["backend_username"],
        "username": settings["backend_username"],
        "password": settings["backend_password"],
        "dashboard_url": settings["dashboard_url"],
        "goods_list_url": settings["goods_list_url"],
        "h5_phone": settings["h5_default_phone"],
        "h5_code": settings["h5_verification_code"],
        "pay_password": settings["pay_password"],
    }
    return {
        key: value
        for key, value in override_map.items()
        if value not in ("", None)
    }


def merge_runtime_parameters(test_data, cli_options, config=None):
    merged_data = deepcopy(test_data or {})
    merged_data.setdefault("parameters", {})
    params = merged_data["parameters"] or {}
    merged_data["parameters"] = params

    override_map = get_runtime_override_map(config)
    cli_dests = {
        option.get("dest")
        for option in (cli_options or [])
        if option.get("dest")
    }

    for dest, value in override_map.items():
        if dest in cli_dests:
            params[dest] = value

    return merged_data


def apply_runtime_config_to_raw_module(raw_module, config=None):
    settings = get_runtime_settings(config)
    override_map = get_runtime_override_map(config)
    constant_aliases = {
        "base_url": ["BASE_URL", "DEFAULT_BASE_URL"],
        "store_id": ["STORE_ID", "DEFAULT_STORE_ID"],
        "phone": ["PHONE", "DEFAULT_PHONE"],
        "password": ["PASSWORD", "DEFAULT_PASSWORD"],
        "h5_phone": ["H5_PHONE", "DEFAULT_H5_PHONE"],
        "h5_code": ["H5_CODE", "DEFAULT_H5_CODE"],
        "pay_password": ["PAY_PASSWORD", "DEFAULT_PAY_PASSWORD"],
    }

    for key, aliases in constant_aliases.items():
        value = override_map.get(key)
        if value in ("", None):
            continue
        for alias in aliases:
            if hasattr(raw_module, alias):
                setattr(raw_module, alias, value)

    configured_dashboard_url = settings.get("dashboard_url", "")
    if (
        configured_dashboard_url
        and _is_dashboard_route(configured_dashboard_url)
        and hasattr(raw_module, "dashboard_url")
        and callable(getattr(raw_module, "dashboard_url"))
    ):
        setattr(raw_module, "dashboard_url", lambda *args, **kwargs: configured_dashboard_url)

    return raw_module
