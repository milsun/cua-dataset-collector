import re

_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


class PrivacyScrubber:
    def __init__(self, config: dict):
        self.config = config["privacy"]
        self._sensitive_app_ids = self.config.get("pause_on_sensitive_apps", [])

    def should_pause(self, bundle_id: str) -> bool:
        return bundle_id in self._sensitive_app_ids

    def scrub_action_data(self, data: dict) -> dict:
        if not self.config.get("enabled", True):
            return data

        scrubbed = dict(data)

        if scrubbed.get("action_type") == "text_input":
            text = scrubbed.get("text", "")
            scrubbed_text, was_scrubbed = self._scrub_text(text)
            scrubbed["text"] = scrubbed_text
            if was_scrubbed:
                scrubbed["scrubbed"] = True

        return scrubbed

    def scrub_window_title(self, title: str) -> str:
        if not self.config.get("enabled", True):
            return title
        result, _ = self._scrub_text(title)
        return result

    def _scrub_text(self, text: str) -> tuple:
        if not text:
            return text, False
        original = text
        text = _CREDIT_CARD_RE.sub("[REDACTED_CC]", text)
        text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        text = _SSN_RE.sub("[REDACTED_SSN]", text)
        return text, text != original
