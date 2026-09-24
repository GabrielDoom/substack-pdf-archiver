import json
import re
from pathlib import Path
import requests
import time

"""
    Runs the PDF download stage.

    Pipeline:

        pdf_urls.json
            ↓
        status='pdf_resolved'
            ↓
        HTTP download
            ↓
        PDF validation
            ↓
        downloads/<source>/
            ↓
        status='downloaded'
    """


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PDF_URLS_FILE = PROJECT_ROOT / "data" / "pdf_urls.json"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3
RETRY_BASE_DELAY = 15
DOWNLOAD_DELAY = 5


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


def sanitize_filename(filename):
    """
    Converts a title or filename into a filesystem-safe name.

    Invalid characters commonly rejected by filesystems are replaced
    with underscores.

    Parameters
    ----------
    filename : str
        Original filename.

    Returns
    -------
    str
        Sanitized filename.
    """

    sanitized = re.sub(
        r'[\\/:*?"<>|]',
        "_",
        filename
    )

    sanitized = re.sub(
        r"\s+",
        " ",
        sanitized
    ).strip()

    if not sanitized.lower().endswith(".pdf"):
        sanitized += ".pdf"

    return sanitized


def get_download_directory(article):
    """
    Returns the directory used to store an article PDF.

    Articles are grouped by their 'source' field.

    Example
    -------
    source='rian-stone'

        downloads/rian-stone/

    Parameters
    ----------
    article : dict
        Article record from pdf_urls.json.

    Returns
    -------
    pathlib.Path
        Destination directory.
    """

    source = article.get("source")

    if not source:
        raise ValueError(
            "Article does not contain a 'source' field."
        )

    directory = DOWNLOADS_DIR / source

    directory.mkdir(
        parents=True,
        exist_ok=True
    )

    return directory


def build_file_path(article):
    """
    Builds the final destination path for one PDF.

    The preferred filename is the PDF title captured by Selenium.
    If that value is unavailable, the original article title is used.

    Parameters
    ----------
    article : dict
        Article record.

    Returns
    -------
    pathlib.Path
        Full destination path.
    """

    raw_filename = (
        article.get("pdf_title")
        or article.get("title")
    )

    if not raw_filename:
        raise ValueError(
            "Article does not contain a usable title."
        )

    filename = sanitize_filename(
        raw_filename
    )

    directory = get_download_directory(
        article
    )

    return directory / filename


def validate_pdf_content(content):
    """
    Validates the raw bytes returned by the HTTP request.

    A valid PDF should begin with the standard '%PDF-' signature.

    Parameters
    ----------
    content : bytes
        HTTP response body.

    Returns
    -------
    bool
        True if the content looks like a PDF.
    """

    return content.startswith(
        b"%PDF-"
    )


def download_pdf(url):
    """
    Downloads one PDF URL through standard HTTP.

    HTTP 429 responses are retried with exponential backoff.
    If the server supplies Retry-After, that value takes precedence.

    Parameters
    ----------
    url : str
        Public Substack PDF endpoint.

    Returns
    -------
    requests.Response
        Successful HTTP response.

    Raises
    ------
    requests.HTTPError
        If the request ultimately fails.
    """

    for attempt in range(
        MAX_RETRIES + 1
    ):
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        if response.status_code != 429:
            response.raise_for_status()
            return response

        if attempt == MAX_RETRIES:
            response.raise_for_status()

        retry_after = (
            response.headers.get(
                "Retry-After"
            )
        )

        if retry_after:
            try:
                delay = int(
                    retry_after
                )
            except ValueError:
                delay = (
                    RETRY_BASE_DELAY
                    * (2 ** attempt)
                )
        else:
            delay = (
                RETRY_BASE_DELAY
                * (2 ** attempt)
            )

        print(
            f"[WAIT] Substack rate limit. "
            f"Retrying in {delay}s..."
        )

        time.sleep(
            delay
        )


def write_pdf(path, content):
    """
    Writes validated PDF bytes to disk.

    Parameters
    ----------
    path : pathlib.Path
        Destination file path.

    content : bytes
        Validated PDF content.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    path.write_bytes(
        content
    )


def download_article(article):
    """
    Downloads one resolved Substack article.

    Workflow:

        article
            ↓
        validate metadata
            ↓
        build destination path
            ↓
        skip if file already exists
            ↓
        HTTP GET
            ↓
        validate Content-Type
            ↓
        validate %PDF signature
            ↓
        write file

    Parameters
    ----------
    article : dict
        Article record whose status should be 'pdf_resolved'.

    Returns
    -------
    dict
        A copy of the article record updated with:

        - status
        - local_path
        - downloaded_size

    Raises
    ------
    ValueError
        If required article fields are missing.

    RuntimeError
        If the downloaded resource is not a valid PDF.
    """

    pdf_url = article.get(
        "pdf_url"
    )

    if not pdf_url:
        raise ValueError(
            "Article does not contain a 'pdf_url'."
        )

    destination = build_file_path(
        article
    )

    result = article.copy()

    if destination.exists():
        result["status"] = "downloaded"
        result["local_path"] = str(
            destination.relative_to(
                PROJECT_ROOT
            )
        )
        result["downloaded_size"] = (
            destination.stat().st_size
        )

        print(
            f"[SKIP] Already downloaded: "
            f"{destination}"
        )

        return result

    response = download_pdf(
        pdf_url
    )

    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
    )

    if "application/pdf" not in content_type:
        raise RuntimeError(
            "Unexpected Content-Type: "
            f"{content_type or 'missing'}"
        )

    if not validate_pdf_content(
        response.content
    ):
        raise RuntimeError(
            "Downloaded content does not begin "
            "with the PDF signature."
        )

    write_pdf(
        destination,
        response.content
    )

    result["status"] = "downloaded"
    result["local_path"] = str(
        destination.relative_to(
            PROJECT_ROOT
        )
    )
    result["downloaded_size"] = len(
        response.content
    )

    print(
        f"[OK] Downloaded:"
    )
    print(
        f"     {destination}"
    )

    return result


def upsert_result(results, article):
    """
    Inserts or replaces one article record.

    post_url is used as the stable identifier.

    Parameters
    ----------
    results : list
        Existing article records.

    article : dict
        Updated article record.
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


def process_downloads(records):
    """
    Downloads all records eligible for the download stage.

    Only articles with status='pdf_resolved' are processed.

    Already downloaded files are recognized by their expected local
    path and are not downloaded again.

    The JSON state file is updated after each successful article.

    Parameters
    ----------
    records : list
        Article records loaded from pdf_urls.json.

    Returns
    -------
    list
        Updated article records.
    """

    results = records.copy()

    eligible = [
        article
        for article in records
        if article.get("status")
        == "pdf_resolved"
    ]

    print(
        f"[INFO] PDFs ready for download: "
        f"{len(eligible)}"
    )

    for article in eligible:
        try:
            downloaded = download_article(
                article
            )

            upsert_result(
                results,
                downloaded
            )

            save_json(
                PDF_URLS_FILE,
                results
            )

            time.sleep(
                DOWNLOAD_DELAY
            )

        except Exception as error:
            print(
                "[ERROR] Could not download:"
            )
            print(
                f"        {article.get('post_url')}"
            )
            print(
                f"        {error}"
            )

    return results


def run_downloads():
    """
    Runs the PDF download stage.
    """

    records = load_json(
        PDF_URLS_FILE,
        default=[]
    )

    if not records:
        print(
            "[INFO] No resolved PDFs found."
        )
        return

    # print(
    #     f"[INFO] Records loaded: "
    #     f"{len(records)}"
    # )

    process_downloads(
        records
    )

    print(
        "[OK] PDF download stage finished."
    )


def main():
    run_downloads()


if __name__ == "__main__":
    main()
    