"""Automated visual smoke-check for an own Avito seller profile and item page.

The script intentionally does not try to bypass CAPTCHA, authorization,
anti-bot checks, or any other website restriction. If a restriction page is
recognized, execution stops and the reason is written to the log.
"""

from __future__ import annotations

import argparse
import configparser
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

BROWSEC_EDGE_EXTENSION_ID = "fjnehcbecaggobjholekjijaaekbnlgj"
BROWSEC_CHROME_EXTENSION_ID = "omghfjlpggmjjaagoclmmobgdodcjboh"
BROWSEC_EXTENSION_ID = BROWSEC_EDGE_EXTENSION_ID
KNOWN_BROWSEC_EXTENSION_IDS = (BROWSEC_EDGE_EXTENSION_ID, BROWSEC_CHROME_EXTENSION_ID)
DEFAULT_VPN_COUNTRIES = (
    "Австрия",
    "Бельгия",
    "Болгария",
    "Великобритания",
    "Германия",
    "Дания",
    "Испания",
    "Италия",
    "Канада",
    "Нидерланды",
    "Норвегия",
    "Польша",
    "Румыния",
    "США",
    "Финляндия",
    "Франция",
    "Чехия",
    "Швейцария",
    "Швеция",
)
DEFAULT_VPN_IP_CHECK_URL = "https://api.ipify.org?format=json"
BROWSEC_POPUP_PATHS = ("popup.html", "popup/index.html", "index.html")
DEFAULT_CONFIG_FILE = "config.ini"

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
    min_ad_open_seconds: int
    max_ad_open_seconds: int
    page_timeout: int
    edge_driver_path: str | None
    edge_user_data_dir: str | None
    edge_profile_directory: str | None
    headless: bool
    enable_vpn: bool
    vpn_extension_id: str
    vpn_extension_id_was_explicit: bool
    chrome_browsec_extension_id: str
    vpn_countries: tuple[str, ...]
    vpn_timeout: int
    vpn_ip_check_url: str
    test_browsec: bool
    browsec_test_hold_seconds: int


def configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


def was_arg_passed(option_name: str) -> bool:
    """Return whether an option was provided on the command line."""
    return any(arg == option_name or arg.startswith(f"{option_name}=") for arg in sys.argv[1:])


def load_config(config_file: str) -> configparser.ConfigParser:
    """Load optional INI defaults without requiring a config file to exist."""
    config = configparser.ConfigParser(interpolation=None)
    read_files = config.read(config_file, encoding="utf-8")
    if read_files:
        logging.debug("Loaded configuration defaults from %s", read_files[0])
    return config


def config_value(
    config: configparser.ConfigParser,
    section: str,
    option: str,
    default: str | None = None,
) -> str | None:
    if config.has_option(section, option):
        return config.get(section, option)
    return default


def config_int(config: configparser.ConfigParser, section: str, option: str, default: int) -> int:
    if config.has_option(section, option):
        return config.getint(section, option)
    return default


def config_bool(config: configparser.ConfigParser, section: str, option: str, default: bool = False) -> bool:
    if config.has_option(section, option):
        return config.getboolean(section, option)
    return default


def parse_config_file_arg() -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--config-file",
        default=DEFAULT_CONFIG_FILE,
        help="Optional INI file with defaults for Edge, VPN, Avito, timing, and logging options",
    )
    args, _ = parser.parse_known_args()
    return args.config_file


def parse_args() -> Settings:
    config_file = parse_config_file_arg()
    config = load_config(config_file)

    parser = argparse.ArgumentParser(
        description=(
            "Open an own Avito profile and item page in Microsoft Edge, then "
            "check item photos one by one without bypassing site restrictions."
        )
    )
    parser.add_argument(
        "--config-file",
        default=config_file,
        help="Optional INI file with defaults for Edge, VPN, Avito, timing, and logging options",
    )
    parser.add_argument(
        "--profile-url",
        default=config_value(config, "avito", "profile_url", DEFAULT_PROFILE_URL),
        help="Avito seller profile URL",
    )
    parser.add_argument(
        "--item-url",
        default=config_value(config, "avito", "item_url", DEFAULT_ITEM_URL),
        help="Avito item URL",
    )
    parser.add_argument(
        "--min-pause",
        type=int,
        default=config_int(config, "timing", "min_pause", 15),
        help="Minimum pause per photo, seconds",
    )
    parser.add_argument(
        "--max-pause",
        type=int,
        default=config_int(config, "timing", "max_pause", 30),
        help="Maximum pause per photo, seconds",
    )
    parser.add_argument(
        "--min-ad-open-seconds",
        type=int,
        default=config_int(config, "timing", "min_ad_open_seconds", 30),
        help="Minimum time to keep the item page open before closing, seconds",
    )
    parser.add_argument(
        "--max-ad-open-seconds",
        type=int,
        default=config_int(config, "timing", "max_ad_open_seconds", 50),
        help="Maximum time to keep the item page open before closing, seconds",
    )
    parser.add_argument(
        "--page-timeout",
        type=int,
        default=config_int(config, "timing", "page_timeout", 45),
        help="Page load timeout, seconds",
    )
    parser.add_argument(
        "--edge-driver-path",
        default=config_value(config, "edge", "driver_path"),
        help="Optional path to msedgedriver",
    )
    parser.add_argument(
        "--edge-user-data-dir",
        default=config_value(config, "edge", "user_data_dir"),
        help="Optional Edge user data directory with the VPN extension installed",
    )
    parser.add_argument(
        "--edge-profile-directory",
        default=config_value(config, "edge", "profile_directory"),
        help="Optional Edge profile directory name, for example 'Default'",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=config_bool(config, "edge", "headless", False),
        help="Run Edge in headless mode",
    )
    parser.add_argument(
        "--enable-vpn",
        action="store_true",
        default=config_bool(config, "vpn", "enable", False),
        help="Enable Browsec VPN and pick a random working location before opening Avito",
    )
    parser.add_argument(
        "--vpn-extension-id",
        default=config_value(config, "vpn", "extension_id", BROWSEC_EXTENSION_ID),
        help=(
            "Browsec extension ID installed in the selected Edge profile. "
            "Default is the Microsoft Edge Add-ons ID; the Chrome Web Store ID is often blocked in Edge."
        ),
    )
    parser.add_argument(
        "--chrome-browsec-extension-id",
        default=config_value(config, "vpn", "chrome_browsec_extension_id", BROWSEC_CHROME_EXTENSION_ID),
        help="Known Chrome Web Store Browsec ID, used only to explain ERR_BLOCKED_BY_CLIENT diagnostics",
    )
    parser.add_argument(
        "--vpn-countries",
        default=config_value(config, "vpn", "countries", ",".join(DEFAULT_VPN_COUNTRIES)),
        help="Comma-separated Browsec locations to try in random order",
    )
    parser.add_argument(
        "--vpn-timeout",
        type=int,
        default=config_int(config, "vpn", "timeout", 30),
        help="VPN setup timeout per location, seconds",
    )
    parser.add_argument(
        "--vpn-ip-check-url",
        default=config_value(config, "vpn", "ip_check_url", DEFAULT_VPN_IP_CHECK_URL),
        help="URL used to verify that the selected VPN location has internet access",
    )
    parser.add_argument(
        "--test-browsec",
        action="store_true",
        default=config_bool(config, "vpn", "test_browsec", False),
        help=(
            "Only launch Edge, open the Browsec extension, turn protection on, "
            "verify internet access, then keep the browser open for manual inspection."
        ),
    )
    parser.add_argument(
        "--browsec-test-hold-seconds",
        type=int,
        default=config_int(config, "vpn", "test_hold_seconds", 30),
        help="How long to keep Edge open after --test-browsec succeeds, seconds",
    )
    parser.add_argument(
        "--log-file",
        default=config_value(config, "logging", "log_file", "avito_ad_check.log"),
        help="Optional log file path",
    )

    args = parser.parse_args()
    configure_logging(args.log_file)

    if config.sections():
        logging.info("Loaded configuration defaults from %s", args.config_file)

    if args.min_pause < 0 or args.max_pause < 0:
        raise AvitoCheckError("Pause values must be non-negative.")
    if args.min_pause > args.max_pause:
        raise AvitoCheckError("--min-pause cannot be greater than --max-pause.")
    if args.min_ad_open_seconds < 0 or args.max_ad_open_seconds < 0:
        raise AvitoCheckError("Ad open time values must be non-negative.")
    if args.min_ad_open_seconds > args.max_ad_open_seconds:
        raise AvitoCheckError("--min-ad-open-seconds cannot be greater than --max-ad-open-seconds.")
    if args.vpn_timeout < 1:
        raise AvitoCheckError("--vpn-timeout must be at least 1 second.")
    if args.browsec_test_hold_seconds < 0:
        raise AvitoCheckError("--browsec-test-hold-seconds must be non-negative.")

    vpn_countries = tuple(country.strip() for country in args.vpn_countries.split(",") if country.strip())
    if args.enable_vpn and not vpn_countries:
        raise AvitoCheckError("At least one VPN country must be provided when --enable-vpn is used.")

    return Settings(
        profile_url=args.profile_url,
        item_url=args.item_url,
        min_pause=args.min_pause,
        max_pause=args.max_pause,
        min_ad_open_seconds=args.min_ad_open_seconds,
        max_ad_open_seconds=args.max_ad_open_seconds,
        page_timeout=args.page_timeout,
        edge_driver_path=args.edge_driver_path,
        edge_user_data_dir=args.edge_user_data_dir,
        edge_profile_directory=args.edge_profile_directory,
        headless=args.headless,
        enable_vpn=args.enable_vpn,
        vpn_extension_id=args.vpn_extension_id,
        vpn_extension_id_was_explicit=(
            was_arg_passed("--vpn-extension-id") or config.has_option("vpn", "extension_id")
        ),
        chrome_browsec_extension_id=args.chrome_browsec_extension_id,
        vpn_countries=vpn_countries,
        vpn_timeout=args.vpn_timeout,
        vpn_ip_check_url=args.vpn_ip_check_url,
        test_browsec=args.test_browsec,
        browsec_test_hold_seconds=args.browsec_test_hold_seconds,
    )


def start_edge(settings: Settings) -> WebDriver:
    logging.info("Launching Microsoft Edge")
    options = EdgeOptions()
    if settings.headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--window-size=1366,900")
    if settings.edge_user_data_dir:
        options.add_argument(f"--user-data-dir={settings.edge_user_data_dir}")
    if settings.edge_profile_directory:
        options.add_argument(f"--profile-directory={settings.edge_profile_directory}")

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


def is_edge_blocked_extension_page(body_text: str) -> bool:
    blocked_markers = (
        "err_blocked_by_client",
        "blocked by client",
        "blocked by microsoft edge",
        "эта страница заблокирована microsoft edge",
        "заблокирована",
    )
    return any(marker in body_text for marker in blocked_markers)


def browsec_extension_ids_to_try(settings: Settings) -> tuple[str, ...]:
    """Return Browsec IDs to probe, preserving user intent and stable order."""
    if settings.vpn_extension_id_was_explicit:
        return (settings.vpn_extension_id,)

    ids: list[str] = []
    for extension_id in (settings.vpn_extension_id, *KNOWN_BROWSEC_EXTENSION_IDS):
        if extension_id and extension_id not in ids:
            ids.append(extension_id)
    return tuple(ids)


def blocked_browsec_message(settings: Settings, popup_url: str, extension_id: str) -> str:
    message = (
        f"Microsoft Edge blocked or could not load the Browsec extension page: {popup_url}. "
        "This usually means the selected Selenium Edge profile does not have Browsec installed/enabled, "
        "or Browsec is installed under a different extension ID. Selenium opens a clean temporary "
        "profile unless you pass --edge-user-data-dir and --edge-profile-directory for the profile "
        "where Browsec is installed."
    )
    if extension_id == BROWSEC_EDGE_EXTENSION_ID:
        message += (
            f" The blocked ID ({extension_id}) is the Microsoft Edge Add-ons ID, so reinstalling "
            "Browsec in the exact Edge profile used by this script is more likely to help than "
            "passing the same ID again."
        )
    elif extension_id == settings.chrome_browsec_extension_id:
        message += (
            f" The blocked ID ({extension_id}) is the Chrome Web Store Browsec ID; if you installed "
            "Browsec from Microsoft Edge Add-ons, use "
            f"--vpn-extension-id {BROWSEC_EDGE_EXTENSION_ID} instead."
        )
    return message


def open_browsec_popup(driver: WebDriver, settings: Settings) -> None:
    last_error: Exception | None = None
    last_body_text = ""
    blocked_messages: list[str] = []
    extension_ids = browsec_extension_ids_to_try(settings)

    for extension_id in extension_ids:
        for path in BROWSEC_POPUP_PATHS:
            popup_url = f"chrome-extension://{extension_id}/{path}"
            try:
                logging.info("Opening Browsec popup page: %s", popup_url)
                driver.get(popup_url)
                WebDriverWait(driver, settings.vpn_timeout).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                last_body_text = body_text.replace("\n", " ")[:500]
                if is_edge_blocked_extension_page(body_text):
                    blocked_messages.append(blocked_browsec_message(settings, popup_url, extension_id))
                    break
                if "err_file_not_found" not in body_text and "this site can" not in body_text:
                    if extension_id != settings.vpn_extension_id:
                        logging.info("Using detected Browsec extension ID: %s", extension_id)
                    return
            except (TimeoutException, WebDriverException) as exc:
                last_error = exc

    details = " ".join(blocked_messages) if blocked_messages else ""
    raise AvitoCheckError(
        "Could not open the Browsec extension popup. Make sure Browsec is installed and enabled in the "
        "selected Edge profile, then pass --edge-user-data-dir/--edge-profile-directory for that profile. "
        "If Browsec was installed from a different store, pass the installed extension ID with "
        "--vpn-extension-id. "
        f"Tried extension IDs: {', '.join(extension_ids)}. "
        f"Last page text: {last_body_text!r}. Last error: {last_error}. {details}"
    )


def click_text_match(driver: WebDriver, text_pattern: str, timeout: int) -> bool:
    script = r'''
        const pattern = arguments[0];
        const rx = new RegExp(pattern, 'i');
        const roots = [document];
        for (let index = 0; index < roots.length; index += 1) {
            for (const element of roots[index].querySelectorAll('*')) {
                if (element.shadowRoot) {
                    roots.push(element.shadowRoot);
                }
            }
        }

        const selector = 'button,a,[role="button"],label,input,[role="switch"]';
        for (const root of roots) {
            const candidates = Array.from(root.querySelectorAll(selector));
            for (const element of candidates) {
                const rect = element.getBoundingClientRect();
                const visible = rect.width > 0 && rect.height > 0;
                const text = (
                    element.innerText ||
                    element.textContent ||
                    element.value ||
                    element.getAttribute('aria-label') ||
                    element.getAttribute('title') ||
                    ''
                ).trim();
                if (!visible || !rx.test(text)) {
                    continue;
                }
                element.click();
                return text;
            }
        }
        return null;
    '''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        clicked_text = driver.execute_script(script, text_pattern)
        if clicked_text:
            logging.info("Clicked extension control matching %r: %s", text_pattern, clicked_text)
            return True
        time.sleep(0.5)
    return False


def is_browsec_enabled(driver: WebDriver) -> bool:
    body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    enabled_markers = (
        "защита включена",
        "protection enabled",
        "protected",
        "browsec is on",
        "vpn is on",
    )
    disabled_markers = (
        "защита выключена",
        "protection disabled",
        "not protected",
        "browsec is off",
        "vpn is off",
    )
    if any(marker in body_text for marker in disabled_markers):
        return False
    if any(marker in body_text for marker in enabled_markers):
        return True

    switch_state = driver.execute_script(
        r'''
        const roots = [document];
        for (let index = 0; index < roots.length; index += 1) {
            for (const element of roots[index].querySelectorAll('*')) {
                if (element.shadowRoot) {
                    roots.push(element.shadowRoot);
                }
            }
        }
        for (const root of roots) {
            const switches = root.querySelectorAll('[role="switch"], input[type="checkbox"]');
            for (const element of switches) {
                const rect = element.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) {
                    continue;
                }
                if (element.getAttribute('aria-checked') === 'true' || element.checked === true) {
                    return true;
                }
            }
        }
        return false;
        '''
    )
    return bool(switch_state)


def click_browsec_power_control(driver: WebDriver, timeout: int) -> bool:
    script = r'''
        const roots = [document];
        for (let index = 0; index < roots.length; index += 1) {
            for (const element of roots[index].querySelectorAll('*')) {
                if (element.shadowRoot) {
                    roots.push(element.shadowRoot);
                }
            }
        }

        const selectors = [
            '[role="switch"]',
            'input[type="checkbox"]',
            'button[aria-label*="on" i]',
            'button[aria-label*="enable" i]',
            'button[aria-label*="вкл" i]',
            'button[class*="power" i]',
            'button[class*="switch" i]',
            'button[class*="toggle" i]',
            '[class*="power" i] button',
            '[class*="switch" i] button',
            '[class*="toggle" i] button',
            '[data-test*="power" i]',
            '[data-testid*="power" i]',
            '[data-test*="switch" i]',
            '[data-testid*="switch" i]',
            '[data-test*="toggle" i]',
            '[data-testid*="toggle" i]'
        ];
        for (const root of roots) {
            for (const selector of selectors) {
                for (const element of root.querySelectorAll(selector)) {
                    const rect = element.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) {
                        continue;
                    }
                    const disabled = element.disabled || element.getAttribute('aria-disabled') === 'true';
                    if (disabled) {
                        continue;
                    }
                    element.click();
                    return selector;
                }
            }
        }
        return null;
    '''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        clicked_selector = driver.execute_script(script)
        if clicked_selector:
            logging.info("Clicked Browsec power control with selector: %s", clicked_selector)
            return True
        time.sleep(0.5)
    return False


def ensure_browsec_enabled(driver: WebDriver, timeout: int) -> None:
    if is_browsec_enabled(driver):
        logging.info("Browsec already reports that protection is enabled")
        return

    clicked = click_browsec_power_control(driver, timeout) or click_text_match(
        driver, r"^\s*(вкл|включить|turn on|enable|on)\s*$", timeout
    )
    if clicked:
        WebDriverWait(driver, timeout).until(lambda d: is_browsec_enabled(d))
        logging.info("Browsec reports that protection is enabled")
        return

    page_text = driver.find_element(By.TAG_NAME, "body").text.replace("\n", " ")[:500]
    raise AvitoCheckError(
        "Could not find the Browsec on/off control in the extension popup. "
        f"Visible popup text starts with: {page_text!r}"
    )


def select_browsec_country(driver: WebDriver, country: str, timeout: int) -> None:
    click_text_match(driver, r"сменить|change|location|country|страна", 3)
    if not click_text_match(driver, re.escape(country), timeout):
        raise AvitoCheckError(f"VPN country was not found in the Browsec popup: {country}")
    time.sleep(2)


def verify_vpn_connection(driver: WebDriver, settings: Settings, country: str) -> bool:
    original_handle = driver.current_window_handle
    driver.switch_to.new_window("tab")
    try:
        separator = "&" if "?" in settings.vpn_ip_check_url else "?"
        check_url = f"{settings.vpn_ip_check_url}{separator}_={int(time.time())}"
        logging.info("Verifying VPN internet access for %s via %s", country, settings.vpn_ip_check_url)
        driver.get(check_url)
        WebDriverWait(driver, settings.vpn_timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        body_text = driver.find_element(By.TAG_NAME, "body").text
        if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|[0-9a-f:]{8,}", body_text, flags=re.IGNORECASE):
            logging.info("VPN location %s is working; public IP response: %s", country, body_text[:120])
            return True
        logging.warning("VPN location %s did not return an IP response: %s", country, body_text[:120])
        return False
    except (TimeoutException, WebDriverException) as exc:
        logging.warning("VPN location %s failed the connectivity check: %s", country, exc)
        return False
    finally:
        driver.close()
        if original_handle in driver.window_handles:
            driver.switch_to.window(original_handle)
        elif driver.window_handles:
            driver.switch_to.window(driver.window_handles[0])


def enable_random_working_vpn(driver: WebDriver, settings: Settings) -> None:
    if not settings.enable_vpn:
        return

    countries = list(settings.vpn_countries)
    random.shuffle(countries)
    logging.info("VPN setup is enabled; trying Browsec locations in random order")

    for country in countries:
        try:
            open_browsec_popup(driver, settings)
            ensure_browsec_enabled(driver, settings.vpn_timeout)
            select_browsec_country(driver, country, settings.vpn_timeout)
            ensure_browsec_enabled(driver, settings.vpn_timeout)
            if verify_vpn_connection(driver, settings, country):
                logging.info("Selected working VPN location before opening Avito: %s", country)
                return
        except AvitoCheckError as exc:
            logging.warning("VPN location %s could not be selected: %s", country, exc)

    raise AvitoCheckError("Could not select any working Browsec VPN location before opening Avito.")


def run_browsec_test(driver: WebDriver, settings: Settings) -> None:
    logging.info("Starting standalone Browsec test")
    open_browsec_popup(driver, settings)
    ensure_browsec_enabled(driver, settings.vpn_timeout)

    if not verify_vpn_connection(driver, settings, "current Browsec location"):
        raise AvitoCheckError("Browsec was enabled, but the IP connectivity check did not return an IP address.")

    logging.info("Standalone Browsec test completed successfully")
    if settings.browsec_test_hold_seconds:
        logging.info(
            "Keeping browser open for %s seconds for manual inspection",
            settings.browsec_test_hold_seconds,
        )
        time.sleep(settings.browsec_test_hold_seconds)


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

    image_count = count_unique_image_sources(
        visible_elements(driver, ('[role="dialog"] img', '[data-marker*="gallery"] img'))
    )
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


def wait_then_close_ad_and_clear_cookies(driver: WebDriver, settings: Settings) -> None:
    delay = random.randint(settings.min_ad_open_seconds, settings.max_ad_open_seconds)
    logging.info(
        "Item page is open; starting auto-close wait for %s seconds (configured range: %s-%s seconds)",
        delay,
        settings.min_ad_open_seconds,
        settings.max_ad_open_seconds,
    )
    time.sleep(delay)

    logging.info("Clearing browser cookies before closing the item page")
    driver.delete_all_cookies()
    logging.info("Cookies cleared")

    window_handles = driver.window_handles
    if len(window_handles) > 1:
        logging.info("Closing current item tab")
        driver.close()
        remaining_handles = driver.window_handles
        if remaining_handles:
            driver.switch_to.window(remaining_handles[0])
    else:
        logging.info("Only one browser window is open; final driver.quit() will close the item page")


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
        if settings.test_browsec:
            run_browsec_test(driver, settings)
            return 0

        enable_random_working_vpn(driver, settings)
        open_page(driver, settings.profile_url, settings.page_timeout, "seller profile")
        open_page(driver, settings.item_url, settings.page_timeout, "item page")
        logging.info("Item page opened successfully")
        browse_photos(driver, settings)
        wait_then_close_ad_and_clear_cookies(driver, settings)
        logging.info("Photo check and item auto-close completed successfully")
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
