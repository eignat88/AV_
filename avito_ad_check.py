"""Automated visual smoke-check for an own Avito seller profile and item page.

The script intentionally does not try to bypass CAPTCHA, authorization,
anti-bot checks, or any other website restriction. If a restriction page is
recognized, execution stops and the reason is written to the log.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from selenium.webdriver.remote.webdriver import WebDriver
    from selenium.webdriver.remote.webelement import WebElement

DEFAULT_PROFILE_URL = (
    "https://www.avito.ru/user/9af9c6083ddc4cebf94872ab7eef5a16/profile"
    "?id=8096214970&iid=8096214970&page_from=from_item_messenger&src=messenger"
)
DEFAULT_ITEM_URL = (
    "https://www.avito.ru/aleksandro-nevskiy/detskaya_odezhda_i_obuv/"
    "botinki_zimnie_tombi_dlya_devochki_25_razmer_8000454499"
    "?slocation=621540&context=H4sIAAAAAAAA_wE_AMD_YToyOntzOjEzOiJsb2NhbFByaW9yaXR5IjtiOjA7"
    "czoxOiJ4IjtzOjE2OiJHdWozQjdnVzRRV3pKV3Z5Ijt9tOTEBT8AAAA"
)

RESTRICTION_PATTERNS = (
    "captcha",
    "капча",
    "подтвердите, что вы не робот",
    "проверяем, что вы не робот",
    "доступ ограничен",
    "access denied",
    "too many requests",
)

PHOTO_OPEN_SELECTORS = (
    '[data-marker="item-view/gallery"] img',
    '[data-marker*="gallery"] img',
    '[data-marker*="image-frame"] img',
    '[data-marker*="item-view/photo"] img',
    'button[aria-label*="фото" i]',
    'button[aria-label*="photo" i]',
    'img[src*="avatars.mds.yandex.net"]',
)

GALLERY_ROOT_SELECTORS = (
    '[data-marker*="gallery"]',
    '[role="dialog"]',
    '[data-marker*="modal"]',
)

NEXT_SELECTORS = (
    'button[aria-label*="След" i]',
    'button[aria-label*="Next" i]',
    '[data-marker*="gallery-next"]',
    '[data-marker*="image-next"]',
    '[data-marker*="next"]',
)

COUNTER_SELECTORS = (
    '[data-marker*="gallery-count"]',
    '[data-marker*="counter"]',
    '[class*="counter"]',
)

THUMBNAIL_SELECTORS = (
    '[data-marker*="gallery-thumbnail"]',
    '[data-marker*="image-preview"]',
    '[data-marker*="preview"] img',
    '[role="dialog"] img',
)


def load_selenium() -> None:
    """Load Selenium after argument parsing so --help works before installation."""
    global webdriver, TimeoutException, WebDriverException, By, Keys
    global EdgeOptions, EdgeService, EC, WebDriverWait

    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait


class AvitoCheckError(RuntimeError):
    """Raised for expected test-flow failures with a clear log message."""


@dataclass(frozen=True)
class Settings:
    profile_url: str
    item_url: str
    min_pause: int
    max_pause: int
    page_timeout: int
    edge_driver_path: str | None
    headless: bool


def configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


def parse_args() -> Settings:
    parser = argparse.ArgumentParser(
        description=(
            "Open an own Avito profile and item page in Microsoft Edge, then "
            "check item photos one by one without bypassing site restrictions."
        )
    )
    parser.add_argument("--profile-url", default=DEFAULT_PROFILE_URL, help="Avito seller profile URL")
    parser.add_argument("--item-url", default=DEFAULT_ITEM_URL, help="Avito item URL")
    parser.add_argument("--min-pause", type=int, default=15, help="Minimum pause per photo, seconds")
    parser.add_argument("--max-pause", type=int, default=30, help="Maximum pause per photo, seconds")
    parser.add_argument("--page-timeout", type=int, default=45, help="Page load timeout, seconds")
    parser.add_argument("--edge-driver-path", help="Optional path to msedgedriver")
    parser.add_argument("--headless", action="store_true", help="Run Edge in headless mode")
    parser.add_argument("--log-file", default="avito_ad_check.log", help="Optional log file path")

    args = parser.parse_args()
    configure_logging(args.log_file)

    if args.min_pause < 0 or args.max_pause < 0:
        raise AvitoCheckError("Pause values must be non-negative.")
    if args.min_pause > args.max_pause:
        raise AvitoCheckError("--min-pause cannot be greater than --max-pause.")

    return Settings(
        profile_url=args.profile_url,
        item_url=args.item_url,
        min_pause=args.min_pause,
        max_pause=args.max_pause,
        page_timeout=args.page_timeout,
        edge_driver_path=args.edge_driver_path,
        headless=args.headless,
    )


def start_edge(settings: Settings) -> WebDriver:
    logging.info("Launching Microsoft Edge")
    options = EdgeOptions()
    if settings.headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--window-size=1366,900")

    service = EdgeService(executable_path=settings.edge_driver_path) if settings.edge_driver_path else EdgeService()
    driver = webdriver.Edge(service=service, options=options)
    driver.set_page_load_timeout(settings.page_timeout)
    return driver


def wait_for_page(driver: WebDriver, timeout: int, page_name: str) -> None:
    logging.info("Waiting for %s to load", page_name)
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    detect_restriction(driver)


def detect_restriction(driver: WebDriver) -> None:
    body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    current_url = driver.current_url.lower()
    for pattern in RESTRICTION_PATTERNS:
        if pattern in body_text or pattern in current_url:
            raise AvitoCheckError(
                "Site restriction was detected (CAPTCHA/blocking/anti-bot page). "
                "The script stops without attempting to bypass it."
            )


def open_page(driver: WebDriver, url: str, timeout: int, page_name: str) -> None:
    logging.info("Opening %s: %s", page_name, url)
    driver.get(url)
    wait_for_page(driver, timeout, page_name)


def visible_elements(driver: WebDriver, selectors: Iterable[str]) -> list[WebElement]:
    result: list[WebElement] = []
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed():
                result.append(element)
    return result


def click_first_photo(driver: WebDriver, timeout: int) -> None:
    logging.info("Opening item photo block")
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        detect_restriction(driver)
        candidates = visible_elements(driver, PHOTO_OPEN_SELECTORS)
        for element in candidates:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                WebDriverWait(driver, 5).until(lambda _: element.is_enabled())
                element.click()
                wait_for_gallery(driver, timeout=10)
                return
            except (WebDriverException, TimeoutException) as exc:
                last_error = exc
        time.sleep(1)

    raise AvitoCheckError(f"Could not open the item photo block. Last error: {last_error}")


def wait_for_gallery(driver: WebDriver, timeout: int) -> None:
    WebDriverWait(driver, timeout).until(lambda d: visible_elements(d, GALLERY_ROOT_SELECTORS))
    detect_restriction(driver)


def parse_photo_count_from_text(text: str) -> int | None:
    matches = re.findall(r"(?:^|\D)(\d{1,3})\s*(?:из|/|of)\s*(\d{1,3})(?:\D|$)", text, flags=re.IGNORECASE)
    if not matches:
        return None
    return max(int(total) for _, total in matches)


def count_unique_image_sources(elements: Iterable[WebElement]) -> int:
    sources: set[str] = set()
    for element in elements:
        src = element.get_attribute("src") or element.get_attribute("data-src")
        if src:
            sources.add(src.split("?")[0])
    return len(sources)


def get_photo_count(driver: WebDriver) -> int:
    for element in visible_elements(driver, COUNTER_SELECTORS):
        count = parse_photo_count_from_text(element.text)
        if count:
            return count

    body_count = parse_photo_count_from_text(driver.find_element(By.TAG_NAME, "body").text)
    if body_count:
        return body_count

    thumbnail_count = len(visible_elements(driver, THUMBNAIL_SELECTORS))
    if thumbnail_count:
        return thumbnail_count

    image_count = count_unique_image_sources(visible_elements(driver, ('[role="dialog"] img', '[data-marker*="gallery"] img')))
    if image_count:
        return image_count

    raise AvitoCheckError("Could not determine the number of item photos.")


def go_to_next_photo(driver: WebDriver) -> None:
    for element in visible_elements(driver, NEXT_SELECTORS):
        try:
            element.click()
            return
        except WebDriverException:
            continue

    logging.info("Next-photo button was not found; sending ArrowRight to the page")
    driver.switch_to.active_element.send_keys(Keys.ARROW_RIGHT)


def browse_photos(driver: WebDriver, settings: Settings) -> None:
    click_first_photo(driver, settings.page_timeout)
    photo_count = get_photo_count(driver)
    logging.info("Found photos: %s", photo_count)

    if photo_count < 1:
        raise AvitoCheckError("No item photos were found.")

    for index in range(1, photo_count + 1):
        detect_restriction(driver)
        pause = random.randint(settings.min_pause, settings.max_pause)
        logging.info("Current photo: %s/%s", index, photo_count)
        logging.info("Waiting on current photo for %s seconds", pause)
        time.sleep(pause)

        if index < photo_count:
            go_to_next_photo(driver)
            time.sleep(1)


def main() -> int:
    try:
        settings = parse_args()
    except AvitoCheckError as exc:
        logging.error("Check stopped: %s", exc)
        return 2

    load_selenium()

    driver: WebDriver | None = None
    try:
        driver = start_edge(settings)
        open_page(driver, settings.profile_url, settings.page_timeout, "seller profile")
        open_page(driver, settings.item_url, settings.page_timeout, "item page")
        browse_photos(driver, settings)
        logging.info("Photo check completed successfully")
        return 0
    except AvitoCheckError as exc:
        logging.error("Check stopped: %s", exc)
        return 2
    except TimeoutException as exc:
        logging.error("Loading timed out or an expected element was unavailable: %s", exc)
        return 3
    except WebDriverException as exc:
        logging.error("Browser/WebDriver error: %s", exc)
        return 4
    finally:
        if driver:
            logging.info("Closing browser")
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
