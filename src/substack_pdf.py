import json
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


"""
    Runs the Substack PDF resolution stage.

    Pipeline:

        articles.json
            ↓
        approved articles
            ↓
        authenticated Firefox
            ↓
        Open as PDF
            ↓
        public PDF URL
            ↓
        pdf_urls.json
    """

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ARTICLES_FILE = PROJECT_ROOT / "data" / "articles.json"
PDF_URLS_FILE = PROJECT_ROOT / "data" / "pdf_urls.json"

FIREFOX_PROFILE = Path(
    "/home/gabriel/.mozilla/firefox/abymjwfg.substack"
)

PAGE_TIMEOUT = 15
MENU_TIMEOUT = 10
PDF_OPTION_TIMEOUT = 5
PDF_NAVIGATION_TIMEOUT = 10


def load_json(path, default=None):
    """
    Reads a JSON file and returns its decoded Python object.

    Parameters
    ----------
    path : pathlib.Path
        JSON file to read.

    default
        Value returned when the file does not exist.

    Returns
    -------
    object
        Decoded JSON content.
    """

    if not path.exists():
        return default

    with path.open(
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def save_json(path, data):
    """
    Writes Python data to a JSON file.

    Parent directories are created automatically.

    Parameters
    ----------
    path : pathlib.Path
        Destination file.

    data
        JSON-serializable Python object.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


def ask_headless_mode():
    """
    Asks whether Firefox should run in headless mode.

    Headless is the default. Pressing Enter therefore returns True.

    Returns
    -------
    bool
        True for headless execution.
        False for visible Firefox.
    """

    while True:
        answer = input(
            "Run Firefox headless? [Y/n]: "
        ).strip().lower()

        if answer in ("", "y", "yes"):
            return True

        if answer in ("n", "no"):
            return False

        print(
            "Please answer Y or N."
        )

def create_driver(headless=False):
    """
    Creates Firefox using the dedicated authenticated
    Substack profile.

    Parameters
    ----------
    headless : bool
        If True, Firefox runs without a visible window.

    Returns
    -------
    selenium.webdriver.Firefox
        Configured Firefox WebDriver.
    """

    if not FIREFOX_PROFILE.exists():
        raise FileNotFoundError(
            f"Firefox profile not found: "
            f"{FIREFOX_PROFILE}"
        )

    options = Options()

    if headless:
        options.add_argument("-headless")

    options.add_argument("-profile")
    options.add_argument(
        str(FIREFOX_PROFILE)
    )

    driver = webdriver.Firefox(
        options=options
    )

    driver.set_page_load_timeout(
        PAGE_TIMEOUT
    )

    return driver


def load_article(driver, url):
    """
    Opens a Substack article and waits until the page
    document is available.

    This function performs navigation only.
    """

    print(f"[INFO] Opening: {url}")

    driver.get(url)

    WebDriverWait(
        driver,
        PAGE_TIMEOUT
    ).until(
        EC.presence_of_element_located(
            (By.TAG_NAME, "body")
        )
    )


def find_more_menu(driver):
    """
    Finds the three-dot article menu.

    The selector is based on properties observed in the
    actual Substack DOM:

    - class contains 'post-ufi-button'
    - aria-haspopup='menu'
    - descendant SVG uses 'lucide-ellipsis'

    Dynamic Radix IDs are intentionally ignored.
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
        return WebDriverWait(
            driver,
            MENU_TIMEOUT
        ).until(
            EC.element_to_be_clickable(
                locator
            )
        )

    except TimeoutException as error:
        raise TimeoutException(
            "Could not locate the authenticated "
            "Substack article menu."
        ) from error


def open_more_menu(driver):
    """
    Finds and opens the article's three-dot menu.
    """

    button = find_more_menu(driver)

    button.click()


def find_open_as_pdf(driver):
    """
    Finds Substack's Open as PDF menu action.

    Both English and Portuguese interface labels are
    supported.

    Returns
    -------
    WebElement
        The clickable button with role='menuitem'.
    """

    locators = [
        (
            By.XPATH,
            "//button[@role='menuitem' "
            "and .//*[normalize-space()='Open as PDF']]"
        ),
        (
            By.XPATH,
            "//button[@role='menuitem' "
            "and .//*[normalize-space()='Abrir como PDF']]"
        ),
    ]

    for locator in locators:
        try:
            return WebDriverWait(
                driver,
                PDF_OPTION_TIMEOUT
            ).until(
                EC.element_to_be_clickable(
                    locator
                )
            )

        except TimeoutException:
            continue

    raise TimeoutException(
        "Could not locate the Substack "
        "'Open as PDF' menu item."
    )


def click_open_as_pdf(driver, pdf_button):
    """
    Clicks the previously located Open as PDF action.

    Returns
    -------
    set
        Firefox window handles existing before the click.
    """

    handles_before = set(
        driver.window_handles
    )

    pdf_button.click()

    return handles_before


def capture_pdf_navigation(
    driver,
    handles_before,
    previous_url
):
    """
    Waits until Substack opens its PDF representation.

    Substack currently navigates in the same tab, but this
    function also supports a future implementation that
    opens the PDF in another tab.

    Returns
    -------
    dict
        Navigation information containing:

        - opened_new_tab
        - pdf_url
        - page_title
    """

    WebDriverWait(
        driver,
        PDF_NAVIGATION_TIMEOUT
    ).until(
        lambda current_driver: (
            len(
                current_driver.window_handles
            ) > len(handles_before)
            or
            current_driver.current_url
            != previous_url
        )
    )

    handles_after = set(
        driver.window_handles
    )

    opened_new_tab = (
        len(handles_after)
        > len(handles_before)
    )

    if opened_new_tab:
        new_handle = next(
            handle
            for handle in handles_after
            if handle not in handles_before
        )

        driver.switch_to.window(
            new_handle
        )

    return {
        "opened_new_tab": opened_new_tab,
        "pdf_url": driver.current_url,
        "page_title": driver.title,
    }


def resolve_pdf(driver, article):
    """
    Resolves one Substack article into its public PDF URL.

    Parameters
    ----------
    driver
        Active authenticated Firefox WebDriver.

    article : dict
        Article record loaded from articles.json.

    Returns
    -------
    dict
        Original article data plus:

        - pdf_url
        - pdf_title
        - status='pdf_resolved'
    """

    post_url = article["post_url"]

    load_article(
        driver,
        post_url
    )

    open_more_menu(driver)

    pdf_button = find_open_as_pdf(
        driver
    )

    previous_url = driver.current_url

    handles_before = click_open_as_pdf(
        driver,
        pdf_button
    )

    navigation = capture_pdf_navigation(
        driver,
        handles_before,
        previous_url
    )

    pdf_url = navigation["pdf_url"]

    if "/api/v1/post/pdf" not in pdf_url:
        raise RuntimeError(
            "Unexpected PDF URL returned by Substack: "
            f"{pdf_url}"
        )

    result = article.copy()

    result["pdf_url"] = pdf_url
    result["pdf_title"] = (
        navigation["page_title"]
    )
    result["status"] = "pdf_resolved"

    return result


def upsert_result(results, article):
    """
    Inserts or replaces one article result.

    post_url is used as the stable identifier.

    This prevents repeated executions from creating
    duplicate records.
    """

    for index, existing in enumerate(
        results
    ):
        if (
            existing.get("post_url")
            == article.get("post_url")
        ):
            results[index] = article
            return

    results.append(article)


def process_articles(
    driver,
    articles,
    existing_results
):
    """
    Resolves all eligible articles.

    Only records with status='approved' are processed.

    Articles that already have status='pdf_resolved'
    in the output file are skipped.

    Results are saved after every successful article so
    that interrupted runs can resume without losing work.
    """

    results = existing_results.copy()

    already_resolved = {
        item.get("post_url")
        for item in results
        if item.get("status")
        in {
            "pdf_resolved",
            "downloaded"
        }
    }

    eligible_articles = [
        article
        for article in articles
        if article.get("status")
        == "approved"
    ]

    print(
        f"[INFO] Approved articles: "
        f"{len(eligible_articles)}"
    )

    for article in eligible_articles:

        post_url = article.get(
            "post_url"
        )

        # Caso 1:
        # O PDF já foi resolvido em execução anterior.
        if post_url in already_resolved:

            article["status"] = "pdf_resolved"

            save_json(
                ARTICLES_FILE,
                articles
            )

            print(
                f"[SKIP] Already resolved: "
                f"{post_url}"
            )

            continue

        # Caso 2:
        # Ainda precisamos resolver o PDF.
        try:
            resolved = resolve_pdf(
                driver,
                article
            )

            upsert_result(
                results,
                resolved
            )

            # Salva os dados específicos do PDF.
            save_json(
                PDF_URLS_FILE,
                results
            )

            # Atualiza também o estado no articles.json.
            article["status"] = "pdf_resolved"

            save_json(
                ARTICLES_FILE,
                articles
            )

            already_resolved.add(
                post_url
            )

            print(
                "[OK] PDF resolved:"
            )

            print(
                f"     {resolved['pdf_url']}"
            )

        except Exception as error:
            print(
                "[ERROR] Could not resolve:"
            )

            print(
                f"        {post_url}"
            )

            print(
                f"        {error}"
            )

    return results


def run_resolution(headless=True):
    """
    Runs the PDF resolution stage.

    Parameters
    ----------
    headless : bool
        Whether Firefox should run without a visible window.
    """

    articles = load_json(
        ARTICLES_FILE,
        default=[]
    )

    if not articles:
        print(
            "[INFO] No articles found."
        )
        return

    existing_results = load_json(
        PDF_URLS_FILE,
        default=[]
    )

    print(
        f"[INFO] Articles loaded: "
        f"{len(articles)}"
    )

    driver = create_driver(
        headless=headless
    )

    try:
        process_articles(
            driver,
            articles,
            existing_results
        )

    finally:
        driver.quit()

    print(
        "[OK] PDF resolution stage finished."
    )

def main():
    
    headless = ask_headless_mode()

    run_resolution(
        headless=headless
    )



if __name__ == "__main__":
    main()