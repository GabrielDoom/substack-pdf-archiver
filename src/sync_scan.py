from gmail_scan import (
    ARTICLES_FILE,
    load_json,
    save_json,
    run_scan,
    article_identity,
)

from substack_pdf import (
    run_resolution,
    select_approved_articles
)

from download_pdfs import (
    run_downloads,
)


def print_new_articles(articles):
    """
    Prints articles awaiting human review.
    """

    if not articles:
        print(
            "[INFO] No articles require review."
        )
        return

    print()
    print("=== ARTICLES TO REVIEW ===")
    print()

    for index, article in enumerate(
        articles,
        start=1
    ):
        print(
            f"[{index}] "
            f"{article['source']}"
        )

        print(
            f"    {article['title']}"
        )

        print(
            f"    {article['email_date']}"
        )

        print()


def parse_selection(selection, total):
    """
    Parses an interactive article selection.

    Accepted inputs
    ---------------
    all
        Select every article.

    none
        Select none.

    1,3,5
        Select specific article numbers.

    2-5
        Select an inclusive range.

    Returns
    -------
    set[int]
        Zero-based article indexes.
    """

    selection = (
        selection
        .strip()
        .lower()
    )

    if selection == "all":
        return set(
            range(total)
        )

    if selection in (
        "",
        "none"
    ):
        return set()

    result = set()

    parts = selection.split(",")

    for part in parts:

        part = part.strip()

        if "-" in part:
            start_text, end_text = (
                part.split(
                    "-",
                    1
                )
            )

            start = int(
                start_text
            )
            end = int(
                end_text
            )

            for number in range(
                start,
                end + 1
            ):
                if 1 <= number <= total:
                    result.add(
                        number - 1
                    )

        else:
            number = int(
                part
            )

            if 1 <= number <= total:
                result.add(
                    number - 1
                )

    return result


def approve_articles(
    all_articles,
    review_articles
):
    """
    Lets the user select which articles awaiting review should
    proceed to Selenium.

    Selected records become:
        approved

    Unselected records remain:
        discovered
    """

    if not review_articles:
        return all_articles

    print_new_articles(
        review_articles
    )

    print(
        "Select articles to approve."
    )
    print(
        "Examples: all | none | 1,3,5 | 2-6"
    )
    print()

    while True:
        try:
            selection = input(
                "Approve: "
            )

            selected_indexes = (
                parse_selection(
                    selection,
                    len(review_articles)
                )
            )

            break

        except ValueError:
            print(
                "Invalid selection. Try again."
            )

    approved_ids = {
        article_identity(
            review_articles[index]
        )
        for index
        in selected_indexes
    }

    updated = []

    for article in all_articles:

        article = article.copy()

        identity = article_identity(
            article
        )

        if identity in approved_ids:
            article["status"] = (
                "approved"
            )

        updated.append(
            article
        )

    return updated


def ask_yes_no(
    prompt,
    default=False
):
    """
    Generic yes/no prompt.

    Parameters
    ----------
    default : bool
        Value returned when the user presses Enter.
    """

    suffix = (
        "[Y/n]"
        if default
        else "[y/N]"
    )

    while True:

        answer = input(
            f"{prompt} {suffix}: "
        ).strip().lower()

        if not answer:
            return default

        if answer in (
            "y",
            "yes"
        ):
            return True

        if answer in (
            "n",
            "no"
        ):
            return False

        print(
            "Please answer Y or N."
        )


def main():
    """
    Runs the complete interactive Substack workflow.

    Gmail
        ↓
    discovery
        ↓
    human approval
        ↓
    optional Selenium
        ↓
    optional download
    """

    all_articles, _ = run_scan()

    review_articles = [
        article
        for article in all_articles
        if article.get("status") == "discovered"
    ]

    if review_articles:
        all_articles = approve_articles(
            all_articles,
            review_articles
        )

        save_json(
            ARTICLES_FILE,
            all_articles
        )

    else:
        print(
            "[INFO] No articles require review."
        )

    approved_count = sum(
        1
        for article in all_articles
        if article.get("status")
        == "approved"
    )

    print()

    print(
        f"[INFO] PDFs awaiting resolution: "
        f"{approved_count}"
    )

    if approved_count == 0:
        print(
            "[OK] Nothing to resolve."
        )
        return


    if not ask_yes_no(
        "Resolve approved articles now?",
        default=False
    ):
        print(
            "[INFO] Articles remain approved "
            "for a later run."
        )
        return


    selected_articles = (
        select_approved_articles(
            all_articles
        )
    )

    if not selected_articles:
        print(
            "[INFO] No articles selected. "
            "Resolution cancelled."
        )
        return


    headless = ask_yes_no(
        "Run Firefox headless?",
        default=True
    )


    resolved_count = run_resolution(
        headless=headless,
        selected_articles=selected_articles
    )


    if resolved_count == 0:
        print(
            "[INFO] No new PDFs were resolved. "
            "Download stage skipped."
        )
        return


    print()

    print(
        f"[OK] Newly resolved PDFs: "
        f"{resolved_count}"
    )


    if ask_yes_no(
        "Download newly resolved PDFs now?",
        default=True
    ):
        run_downloads()

if __name__ == "__main__":
    main()