from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from playwright.sync_api import Page


FeedbackTarget = tuple[str, str]
FeedbackFilter = Callable[[str, str], bool]


DEFAULT_FEEDBACK_TARGETS: tuple[FeedbackTarget, ...] = (
    (
        "modal",
        ".el-message-box:visible, .el-dialog:visible, .ant-modal:visible, [role='dialog']:visible",
    ),
    (
        "toast",
        ".el-message:visible, .ant-message-notice:visible, .toast:visible, [role='alert']:visible",
    ),
    (
        "inline",
        ".el-form-item__error:visible, .ant-form-item-explain-error:visible, "
        ".form-error:visible, .error-message:visible",
    ),
    (
        "banner",
        ".el-alert:visible, .ant-alert:visible, [role='status']:visible",
    ),
)


@dataclass(frozen=True)
class FeedbackObservation:
    presentation: str
    text: str
    selector: str
    appeared_after_ms: int
    auto_dismissed: bool | None
    screenshot_path: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: str) -> str:
    return " ".join((value or "").split())


def snapshot_visible_feedback(
    page: Page,
    targets: Sequence[FeedbackTarget] = DEFAULT_FEEDBACK_TARGETS,
) -> list[tuple[str, str, str]]:
    """Return visible feedback as (presentation, text, selector) tuples."""
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for presentation, selector in targets:
        locator = page.locator(selector)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                candidate_text = _clean_text(candidate.inner_text(timeout=250))
            except Exception:
                continue
            token = (presentation, candidate_text)
            if candidate_text and token not in seen:
                seen.add(token)
                found.append((presentation, candidate_text, selector))
    return found


def _feedback_still_visible(
    page: Page,
    presentation: str,
    feedback_text: str,
    targets: Sequence[FeedbackTarget],
) -> bool:
    return any(
        kind == presentation and text == feedback_text
        for kind, text, _ in snapshot_visible_feedback(page, targets)
    )


def capture_transient_feedback(
    page: Page,
    action: Callable[[], Any],
    *,
    timeout_ms: int = 3000,
    poll_interval_ms: int = 100,
    dismissal_timeout_ms: int = 0,
    screenshot_path: str | Path | None = None,
    accept: FeedbackFilter | None = None,
    targets: Sequence[FeedbackTarget] = DEFAULT_FEEDBACK_TARGETS,
) -> FeedbackObservation | None:
    """Capture newly visible UI feedback after an action.

    The baseline is sampled before ``action`` so persistent dialogs or page
    validation text are not mistaken for the response to the current action.
    ``accept`` should recognize the relevant feedback family, not require the
    exact expected wording; category and field-anchor assertions happen after
    capture so an unexpected generic error remains observable.
    """
    timeout_ms = max(1, int(timeout_ms))
    poll_interval_ms = max(20, int(poll_interval_ms))
    dismissal_timeout_ms = max(0, int(dismissal_timeout_ms))
    baseline = {
        (presentation, feedback_text)
        for presentation, feedback_text, _ in snapshot_visible_feedback(page, targets)
    }
    started = time.monotonic()
    action()
    deadline = started + timeout_ms / 1000

    while time.monotonic() <= deadline:
        for presentation, feedback_text, selector in snapshot_visible_feedback(page, targets):
            if (presentation, feedback_text) in baseline:
                continue
            if accept is not None and not accept(presentation, feedback_text):
                continue

            captured_path = ""
            if screenshot_path is not None:
                output = Path(screenshot_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output), full_page=False, timeout=8000)
                captured_path = str(output)

            appeared_after_ms = max(0, round((time.monotonic() - started) * 1000))
            auto_dismissed: bool | None = None
            if dismissal_timeout_ms:
                dismiss_deadline = time.monotonic() + dismissal_timeout_ms / 1000
                auto_dismissed = False
                while time.monotonic() <= dismiss_deadline:
                    if not _feedback_still_visible(page, presentation, feedback_text, targets):
                        auto_dismissed = True
                        break
                    page.wait_for_timeout(poll_interval_ms)

            return FeedbackObservation(
                presentation=presentation,
                text=feedback_text,
                selector=selector,
                appeared_after_ms=appeared_after_ms,
                auto_dismissed=auto_dismissed,
                screenshot_path=captured_path,
            )
        page.wait_for_timeout(poll_interval_ms)
    return None
