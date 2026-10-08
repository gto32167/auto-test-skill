from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a visible browser, wait for manual backend login, and save Playwright storage_state."
    )
    parser.add_argument("--login-url", required=True)
    parser.add_argument("--success-prefix", required=True, help="URL prefix that indicates backend login succeeded.")
    parser.add_argument("--output", required=True, help="storage_state json output path")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--browser", choices=["chromium", "firefox", "webkit"], default="chromium")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser_type = getattr(p, args.browser)
        browser = browser_type.launch(headless=False, slow_mo=0)
        context = browser.new_context(viewport={"width": 1440, "height": 1200})
        # Reduce obvious automation fingerprints so manual verification can proceed more smoothly.
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        page.goto(args.login_url, wait_until="networkidle", timeout=60000)

        print("Browser opened. Please complete manual login in the visible browser window.")
        print(f"Success will be detected when URL starts with: {args.success_prefix}")

        deadline = time.time() + max(30, args.timeout_seconds)
        last_url = page.url
        while time.time() < deadline:
            try:
                last_url = page.url
                if last_url.startswith(args.success_prefix):
                    context.storage_state(path=str(output_path))
                    print(f"Saved storage_state to: {output_path}")
                    print(f"Detected success URL: {last_url}")
                    return
            except Exception as exc:
                print(f"Polling error: {exc}")
            time.sleep(2)

        raise SystemExit(f"Timed out waiting for login success. Last URL: {last_url}")


if __name__ == "__main__":
    main()
