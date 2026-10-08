__all__ = [
    "ConfigManager",
    "FeedbackObservation",
    "ScriptParser",
    "capture_transient_feedback",
    "snapshot_visible_feedback",
]


def __getattr__(name):
    if name == "ConfigManager":
        from .config_manager import ConfigManager

        return ConfigManager
    if name == "ScriptParser":
        from .script_parser import ScriptParser

        return ScriptParser
    if name in {
        "FeedbackObservation",
        "capture_transient_feedback",
        "snapshot_visible_feedback",
    }:
        from .ui_feedback import (
            FeedbackObservation,
            capture_transient_feedback,
            snapshot_visible_feedback,
        )

        return {
            "FeedbackObservation": FeedbackObservation,
            "capture_transient_feedback": capture_transient_feedback,
            "snapshot_visible_feedback": snapshot_visible_feedback,
        }[name]
    raise AttributeError(f"module 'common' has no attribute {name!r}")
