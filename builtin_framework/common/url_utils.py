from urllib.parse import urlsplit, urlunsplit


def resolve_target_url(recorded_url, config):
    if not recorded_url:
        return recorded_url

    split_result = urlsplit(recorded_url)
    if not split_result.scheme or not split_result.netloc:
        return recorded_url

    recorded_origin = f"{split_result.scheme}://{split_result.netloc}"
    url_mappings = config.get("url_mappings", {}) or {}
    target_origin = url_mappings.get(recorded_origin)
    if not target_origin:
        return recorded_url

    mapped_split = urlsplit(target_origin)
    target_scheme = mapped_split.scheme or split_result.scheme
    target_netloc = mapped_split.netloc or split_result.netloc

    return urlunsplit(
        (
            target_scheme,
            target_netloc,
            split_result.path,
            split_result.query,
            split_result.fragment,
        )
    )
