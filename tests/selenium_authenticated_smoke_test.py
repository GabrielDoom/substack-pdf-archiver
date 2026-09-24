from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


ARTICLE_URL = (
    "https://rianstone.substack.com/p/"
    "a-loose-collection-of-the-cardinal"
)

PROFILE_DIR = Path(
    "/home/gabriel/.mozilla/firefox/abymjwfg.substack"
)

PAGE_TIMEOUT = 15
MENU_TIMEOUT = 10
PDF_OPTION_TIMEOUT = 5


def create_driver(headless=False):
    """
    Opens Firefox using the dedicated authenticated Substack profile.

    The profile must not already be open in another Firefox process.
    """

    if not PROFILE_DIR.exists():
        raise FileNotFoundError(
            f"Firefox profile not found: {PROFILE_DIR}"
        )

    options = Options()

    if headless:
        options.add_argument("-headless")

    options.add_argument("-profile")
    options.add_argument(str(PROFILE_DIR))

    driver = webdriver.Firefox(options=options)

    driver.set_page_load_timeout(PAGE_TIMEOUT)

    return driver


def load_article(driver, url):
    """
    Loads a Substack article and waits for the page body.
    """

    print("[INFO] Opening article:")
    print(f"       {url}")

    driver.get(url)

    WebDriverWait(driver, PAGE_TIMEOUT).until(
        EC.presence_of_element_located(
            (By.TAG_NAME, "body")
        )
    )

    print("[OK] Page loaded.")


def find_more_menu(driver):
    """
    Finds the article action menu by locating a button that:

    - declares aria-haspopup="menu"
    - contains Substack's ellipsis SVG icon

    Dynamic Radix IDs and open/closed state are deliberately ignored.
    """

    locator = (
        By.XPATH,
        "//button["
        "contains(@class, 'post-ufi-button') "
        "and @aria-haspopup='menu' "
        "and .//*[contains(@class, 'lucide-ellipsis')]"
        "]"
    )

    try:
        element = WebDriverWait(
            driver,
            MENU_TIMEOUT
        ).until(
            EC.element_to_be_clickable(locator)
        )

        print("[OK] Three-dot menu found.")

        return element

    except TimeoutException:
        raise TimeoutException(
            "Could not locate the Substack ellipsis menu."
        )

def open_more_menu(driver):
    """
    Opens the article action menu.
    """

    menu = find_more_menu(driver)

    menu.click()

    print("[OK] Three-dot menu opened.")


def find_open_as_pdf(driver):
    """
    Finds the interactive menu item containing the
    'Open as PDF' action.

    Both English and Portuguese UI labels are accepted.
    """

    candidates = [
        (
            By.XPATH,
            "//*[normalize-space()='Open as PDF']"
            "/ancestor::*[@role='menuitem'][1]"
        ),
        (
            By.XPATH,
            "//*[normalize-space()='Abrir como PDF']"
            "/ancestor::*[@role='menuitem'][1]"
        ),
        (
            By.XPATH,
            "//*[normalize-space()='Open as PDF']"
            "/ancestor::button[1]"
        ),
        (
            By.XPATH,
            "//*[normalize-space()='Abrir como PDF']"
            "/ancestor::button[1]"
        ),
    ]

    for locator in candidates:
        try:
            element = WebDriverWait(
                driver,
                PDF_OPTION_TIMEOUT
            ).until(
                EC.element_to_be_clickable(locator)
            )

            print("[OK] 'Open as PDF' action found.")

            return element

        except TimeoutException:
            continue

    raise TimeoutException(
        "Could not locate the interactive 'Open as PDF' menu item."
    )

def main():
    """
    Validates:

        dedicated Firefox profile
            ↓
        authenticated Substack session
            ↓
        public article
            ↓
        three-dot menu
            ↓
        Open as PDF option

    The script deliberately stops before clicking Open as PDF.
    """

    print("[INFO] Firefox profile:")
    print(f"       {PROFILE_DIR}")
    print()

    driver = create_driver(headless=False)

    try:
        load_article(driver, ARTICLE_URL)

        open_more_menu(driver)

        pdf_option = find_open_as_pdf(driver)

        print()
        print("=== AUTHENTICATED SELENIUM TEST ===")
        print("Firefox profile ............ OK")
        print("Article loading ............ OK")
        print("Authenticated menu ......... OK")
        print("Open as PDF option ......... OK")
        print()
        print(
            f"PDF menu element tag: {pdf_option.tag_name}"
        )
        print(
            f"PDF element role: {pdf_option.get_attribute('role')}"
        )
        print(
            f"PDF element class: {pdf_option.get_attribute('class')}"
        )

        input(
            "\nPress Enter to close Firefox..."
        )

    finally:
        driver.quit()


if __name__ == "__main__":
    main()