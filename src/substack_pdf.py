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

class PermanentResolutionError(Exception):
    """
    Indicates that the article cannot be resolved automatically
    with the current Substack workflow.
    """

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


def parse_selection(selection, total):
    """
    Parses article numbers selected by the user.

    Accepted formats:
        all
        none
        1,3,5
        2-6
    """

    selection = selection.strip().lower()

    if selection == "all":
        return set(range(total))

    if selection in ("", "none"):
        return set()

    selected = set()

    for part in selection.split(","):
        part = part.strip()

        if "-" in part:
            start_text, end_text = part.split("-", 1)

            start = int(start_text)
            end = int(end_text)

            for number in range(start, end + 1):
                if 1 <= number <= total:
                    selected.add(number - 1)

        else:
            number = int(part)

            if 1 <= number <= total:
                selected.add(number - 1)

    return selected

def select_approved_articles(articles):
    """
    Lets the user choose which approved articles should be
    resolved during this execution.
    """

    approved = [
        article
        for article in articles
        if article.get("status") == "approved"
    ]

    print(
        f"[INFO] PDFs awaiting resolution: "
        f"{len(approved)}"
    )

    if not approved:
        return []

    print()
    print("=== APPROVED ARTICLES ===")
    print()

    for index, article in enumerate(
        approved,
        start=1
    ):
        print(
            f"[{index}] "
            f"{article['source']}"
        )
        print(
            f"    {article['title']}"
        )
        print()

    print(
        "Select articles to resolve."
    )
    print(
        "Examples: all | none | 1,3,5 | 2-6"
    )

    while True:
        try:
            selection = input(
                "Resolve: "
            )

            indexes = parse_selection(
                selection,
                len(approved)
            )

            break

        except ValueError:
            print(
                "Invalid selection. Try again."
            )

    return [
        approved[index]
        for index in sorted(indexes)
    ]


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

def update_article_status(
    articles,
    target_article,
    status
):
    """
    Updates the matching record in articles.json.

    Gmail message ID is used as the primary identity because it
    remains stable even when a Substack URL changes.
    """

    gmail_id = target_article.get(
        "gmail_id"
    )

    post_url = target_article.get(
        "post_url"
    )

    for article in articles:

        same_gmail_id = (
            gmail_id
            and article.get("gmail_id") == gmail_id
        )

        same_post_url = (
            post_url
            and article.get("post_url") == post_url
        )

        if (
            same_gmail_id
            or same_post_url
        ):
            article["status"] = status
            return True

    return False

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

    current_url = driver.current_url

    print(
        f"[DEBUG] Loaded URL: "
        f"{current_url}"
    )

    if "/p/" not in current_url:
        raise PermanentResolutionError(
            "Substack redirected away from "
            f"the article page: {current_url}"
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

def reset_browser_state(driver):
    """
    Restores Firefox to a clean single-tab state.

    This is used after a failed PDF resolution so the next article
    does not inherit a broken navigation state.
    """

    try:
        handles = driver.window_handles

        if not handles:
            return

        primary_handle = handles[0]

        for handle in handles[1:]:
            try:
                driver.switch_to.window(
                    handle
                )
                driver.close()
            except Exception:
                pass

        driver.switch_to.window(
            primary_handle
        )

        driver.get(
            "about:blank"
        )

    except Exception:
        pass

def process_articles(
    driver,
    articles,
    selected_articles,
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

    resolved_count = 0

    already_resolved = {
        item.get("post_url")
        for item in results
        if item.get("status")
        in {
            "pdf_resolved",
            "downloaded"
        }
    }

    print(
        f"[INFO] Articles selected: "
        f"{len(selected_articles)}"
    )


    for article in selected_articles:

        reset_browser_state(
            driver
        )

        post_url = article.get(
            "post_url"
        )

        if post_url in already_resolved:

            update_article_status(
                articles,
                article,
                "pdf_resolved"
            )

            save_json(
                ARTICLES_FILE,
                articles
            )

            print(
                f"[SKIP] Already resolved: "
                f"{article.get('title')}"
            )

            continue

        try:
            resolved = resolve_pdf(
                driver,
                article
            )

            upsert_result(
                results,
                resolved
            )

            save_json(
                PDF_URLS_FILE,
                results
            )

            update_article_status(
                articles,
                article,
                "pdf_resolved"
            )

            save_json(
                ARTICLES_FILE,
                articles
            )

            already_resolved.add(
                post_url
            )

            resolved_count += 1

            print(
                "[OK] PDF resolved:"
            )
            print(
                f"     {resolved['pdf_url']}"
            )

        except Exception as error:

            error_message = str(error)

            permanent_error = (
                isinstance(
                    error,
                    PermanentResolutionError
                )
                or
                "networkProtocolError"
                in error_message
            )

            if permanent_error:
                article["status"] = "unresolved"

                save_json(
                    ARTICLES_FILE,
                    articles
                )

                print(
                    "[UNRESOLVED] Could not resolve automatically:"
                )

            else:
                print(
                    "[ERROR] Could not resolve:"
                )

            print(
                f"        {post_url}"
            )

            print(
                f"        {error}"
            )

            reset_browser_state(
                driver
            )

    return results, resolved_count


def run_resolution(
    headless=True,
    selected_articles=None
    ):
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
        return 0

    if selected_articles is None:
        selected_articles = (
            select_approved_articles(
                articles
            )
        )


    if not selected_articles:
            print(
                "[INFO] No articles selected. "
                "PDF resolution cancelled."
            )
            return 0

    existing_results = load_json(
        PDF_URLS_FILE,
        default=[]
    )

    # print(
    #     f"[INFO] Articles loaded: "
    #     f"{len(articles)}"
    # )

    driver = create_driver(
        headless=headless
    )

    try:
        results, resolved_count = (
            process_articles(
                driver,
                articles,
                selected_articles,
                existing_results
            )
        )

    finally:
        driver.quit()

    print(
        "[OK] PDF resolution stage finished."
    )

    return resolved_count

def main():
    articles = load_json(
        ARTICLES_FILE,
        default=[]
    )

    if not articles:
        print(
            "[INFO] No articles found."
        )
        return

    selected_articles = (
        select_approved_articles(
            articles
        )
    )

    if not selected_articles:
        print(
            "[INFO] No articles selected."
        )
        return

    headless = ask_headless_mode()

    run_resolution(
        headless=headless,
        selected_articles=selected_articles
    )


if __name__ == "__main__":
    main()