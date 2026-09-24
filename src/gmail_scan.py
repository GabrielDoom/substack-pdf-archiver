import base64
import html
import io
import json
import re
import warnings

from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import argparse


"""
    Runs the Gmail discovery stage.

    Pipeline:

        Gmail API
            ↓
        Substack messages
            ↓
        selected newsletters
            ↓
        article URL extraction
            ↓
        deduplication
            ↓
        articles.json
    """

# ---------------------------------------------------------
# Runtime warnings
# ---------------------------------------------------------

DEBUG = False

if not DEBUG:
    warnings.filterwarnings(
        "ignore",
        category=FutureWarning
    )

    warnings.filterwarnings(
        "ignore",
        message=(
            "Python 3.8 is no longer supported.*"
        )
    )


# Google imports are intentionally placed after warning
# configuration so unsupported-Python warnings do not clutter
# normal application output.

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# ---------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CREDENTIALS_FILE = (
    PROJECT_ROOT
    / "credentials"
    / "credentials.json"
)

TOKEN_FILE = (
    PROJECT_ROOT
    / "credentials"
    / "token.json"
)

SOURCES_FILE = (
    PROJECT_ROOT
    / "config"
    / "sources.json"
)

ARTICLES_FILE = (
    PROJECT_ROOT
    / "data"
    / "articles.json"
)

SCAN_STATE_FILE = (
    PROJECT_ROOT
    / "data"
    / "scan_state.json"
)


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]

# Scheduling

def get_scan_cutoff():
    """
    Determines how far back the scheduled scanner should search.

    The scanner always covers at least the previous 24 hours.
    If the previous successful scan happened earlier than that,
    scanning resumes from that timestamp.
    """

    now = datetime.now(
        timezone.utc
    )

    minimum_cutoff = (
        now - timedelta(hours=24)
    )

    state = load_json(
        SCAN_STATE_FILE,
        default={}
    )

    last_scan_text = state.get(
        "last_successful_scan"
    )

    if not last_scan_text:
        return minimum_cutoff

    try:
        last_scan = datetime.fromisoformat(
            last_scan_text
        )

    except ValueError:
        return minimum_cutoff

    if last_scan.tzinfo is None:
        last_scan = last_scan.replace(
            tzinfo=timezone.utc
        )

    return min(
        minimum_cutoff,
        last_scan
    )

def build_scheduled_query(cutoff):
    """
    Builds a coarse Gmail-side filter.

    Exact timestamp filtering is performed afterwards using
    Gmail's internalDate.
    """

    date_text = cutoff.strftime(
        "%Y/%m/%d"
    )

    return (
        f"in:inbox "
        f"from:substack.com "
        f"after:{date_text}"
    )

# Initial backlog scan.
#
# Later, for the daily synchronous scanner, this can become:
#
#   from:substack.com newer_than:2d
#
# without changing the rest of the program.

GMAIL_QUERY = "in:inbox from:substack.com"


# ---------------------------------------------------------
# Simple logging
# ---------------------------------------------------------

def log_debug(message):
    """
    Prints technical information only when DEBUG is enabled.
    """

    if DEBUG:
        print(f"[DEBUG] {message}")


def log_info(message):
    """
    Prints normal operational information.
    """

    print(f"[INFO] {message}")


def log_ok(message):
    """
    Prints successful stage information.
    """

    print(f"[OK] {message}")


def log_error(message):
    """
    Prints recoverable errors.
    """

    print(f"[ERROR] {message}")


# ---------------------------------------------------------
# JSON storage
# ---------------------------------------------------------

def load_json(path, default=None):
    """
    Reads a JSON file.

    Parameters
    ----------
    path : pathlib.Path
        JSON file path.

    default
        Value returned when the file does not exist.

    Returns
    -------
    object
        Decoded JSON data.
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
    Writes JSON data using UTF-8.

    Parent directories are created automatically.
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

def save_scan_state():
    """
    Records the completion time of a successful scheduled scan.
    """

    now = datetime.now(
        timezone.utc
    )

    save_json(
        SCAN_STATE_FILE,
        {
            "last_successful_scan":
                now.isoformat()
        }
    )


# ---------------------------------------------------------
# Source configuration
# ---------------------------------------------------------

def load_sources():
    """
    Loads newsletter definitions from config/sources.json.

    Returns
    -------
    list
        Configured source dictionaries.
    """

    config = load_json(
        SOURCES_FILE,
        default={}
    )

    sources = config.get(
        "sources",
        []
    )

    if not sources:
        raise RuntimeError(
            "No newsletter sources configured."
        )

    return sources


# ---------------------------------------------------------
# Gmail authentication
# ---------------------------------------------------------

def authenticate_gmail():
    """
    Authenticates with Gmail using OAuth.

    token.json is reused whenever possible.

    A browser authorization flow is started only when a valid
    stored token is unavailable.

    Returns
    -------
    googleapiclient.discovery.Resource
        Authenticated Gmail API service.
    """

    credentials = None

    if TOKEN_FILE.exists():
        credentials = (
            Credentials
            .from_authorized_user_file(
                TOKEN_FILE,
                SCOPES
            )
        )

    if (
        credentials
        and credentials.expired
        and credentials.refresh_token
    ):
        credentials.refresh(
            Request()
        )

    elif not credentials or not credentials.valid:

        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"OAuth credentials not found: "
                f"{CREDENTIALS_FILE}"
            )

        from google_auth_oauthlib.flow import (
            InstalledAppFlow
        )

        flow = (
            InstalledAppFlow
            .from_client_secrets_file(
                CREDENTIALS_FILE,
                SCOPES
            )
        )

        # During ordinary operation token.json already exists,
        # so this branch is rarely reached.
        credentials = flow.run_local_server(
            port=0
        )

        TOKEN_FILE.write_text(
            credentials.to_json(),
            encoding="utf-8"
        )

    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False
    )


# ---------------------------------------------------------
# Gmail search
# ---------------------------------------------------------

def search_messages(service, query):
    """
    Searches Gmail and returns every matching message ID.

    Gmail paginates search results, so this function follows
    nextPageToken until the complete result set has been read.

    Parameters
    ----------
    service
        Authenticated Gmail API service.

    query : str
        Gmail search query.

    Returns
    -------
    list[str]
        Gmail message IDs.
    """

    message_ids = []
    page_token = None

    while True:

        request = (
            service
            .users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=100,
                pageToken=page_token
            )
        )

        response = request.execute()

        messages = response.get(
            "messages",
            []
        )

        message_ids.extend(
            message["id"]
            for message in messages
        )

        page_token = response.get(
            "nextPageToken"
        )

        if not page_token:
            break

    return message_ids


def get_message(service, message_id):
    """
    Retrieves one Gmail message using the full MIME representation.

    Parameters
    ----------
    service
        Gmail API service.

    message_id : str
        Gmail message ID.

    Returns
    -------
    dict
        Gmail message resource.
    """

    return (
        service
        .users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full"
        )
        .execute()
    )

def message_is_after_cutoff(
    message,
    cutoff
):
    """
    Checks Gmail's internal timestamp against the scheduled
    scan cutoff.
    """

    internal_date = message.get(
        "internalDate"
    )

    if not internal_date:
        return True

    message_time = datetime.fromtimestamp(
        int(internal_date) / 1000,
        tz=timezone.utc
    )

    return message_time >= cutoff


# ---------------------------------------------------------
# Header handling
# ---------------------------------------------------------

def get_headers(message):
    """
    Converts Gmail's list of MIME headers into a dictionary.
    """

    headers = (
        message
        .get("payload", {})
        .get("headers", [])
    )

    return {
        item["name"].lower():
        item.get("value", "")
        for item in headers
    }


def extract_email_date(message):
    """
    Extracts the message date in YYYY-MM-DD format.

    The MIME Date header is preferred. Gmail's internal timestamp
    is used as a fallback.

    Returns
    -------
    str | None
    """

    headers = get_headers(
        message
    )

    date_header = headers.get(
        "date"
    )

    if date_header:
        try:
            parsed = parsedate_to_datetime(
                date_header
            )

            return parsed.date().isoformat()

        except (TypeError, ValueError):
            pass

    internal_date = message.get(
        "internalDate"
    )

    if internal_date:
        timestamp = (
            int(internal_date)
            / 1000
        )

        return (
            datetime
            .fromtimestamp(timestamp)
            .date()
            .isoformat()
        )

    return None


# ---------------------------------------------------------
# MIME body extraction
# ---------------------------------------------------------

def decode_body_data(data):
    """
    Decodes Gmail's URL-safe Base64 MIME body data.

    Returns
    -------
    str
        UTF-8 text.
    """

    if not data:
        return ""

    padding = "=" * (
        -len(data) % 4
    )

    decoded = (
        base64
        .urlsafe_b64decode(
            data + padding
        )
    )

    return decoded.decode(
        "utf-8",
        errors="replace"
    )


def extract_message_bodies(part):
    """
    Recursively extracts text/plain and text/html MIME bodies.

    Parameters
    ----------
    part : dict
        Gmail MIME payload or sub-part.

    Returns
    -------
    dict
        {
            "plain": [...],
            "html": [...]
        }
    """

    result = {
        "plain": [],
        "html": []
    }

    mime_type = part.get(
        "mimeType",
        ""
    )

    data = (
        part
        .get("body", {})
        .get("data")
    )

    if data:

        decoded = decode_body_data(
            data
        )

        if mime_type == "text/plain":
            result["plain"].append(
                decoded
            )

        elif mime_type == "text/html":
            result["html"].append(
                decoded
            )

    for child in part.get(
        "parts",
        []
    ):

        child_result = (
            extract_message_bodies(
                child
            )
        )

        result["plain"].extend(
            child_result["plain"]
        )

        result["html"].extend(
            child_result["html"]
        )

    return result


# ---------------------------------------------------------
# HTML URL extraction
# ---------------------------------------------------------

class LinkExtractor(HTMLParser):
    """
    Minimal HTML parser that collects href attributes.
    """

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(
        self,
        tag,
        attrs
    ):
        if tag.lower() != "a":
            return

        for name, value in attrs:

            if (
                name.lower() == "href"
                and value
            ):
                self.links.append(
                    html.unescape(value)
                )


def extract_html_links(html_content):
    """
    Extracts hyperlinks from HTML email content.
    """

    parser = LinkExtractor()

    try:
        parser.feed(
            html_content
        )

    except Exception:
        return []

    return parser.links


# ---------------------------------------------------------
# Substack URL handling
# ---------------------------------------------------------

def normalize_post_url(url):
    """
    Normalizes a candidate Substack post URL.

    Tracking query parameters and fragments are removed.

    A URL is accepted only when:

    - it uses HTTP/HTTPS;
    - the host belongs to Substack;
    - the path represents a post and contains '/p/'.

    Parameters
    ----------
    url : str

    Returns
    -------
    str | None
        Normalized post URL.
    """

    if not url:
        return None

    url = html.unescape(
        url.strip()
    )

    try:
        parsed = urlsplit(
            url
        )

    except ValueError:
        return None

    if parsed.scheme not in {
        "http",
        "https"
    }:
        return None

    host = (
        parsed.hostname
        or ""
    ).lower()

    if (
        "substack.com"
        not in host
    ):
        return None

    path = parsed.path

    if "/p/" not in path:
        return None

    return urlunsplit(
        (
            "https",
            parsed.netloc,
            path.rstrip("/"),
            "",
            ""
        )
    )


def extract_post_url(message):
    """
    Finds the most likely primary Substack article URL in an email.

    Strategy
    --------
    1. Extract every href from HTML.
    2. Extract raw URLs from plain text as a fallback.
    3. Normalize Substack post URLs.
    4. Count duplicate occurrences.
    5. Select the most frequently referenced post.

    Substack normally links the primary post several times
    (headline, image, read button), making frequency a useful signal.

    Returns
    -------
    str | None
    """

    bodies = extract_message_bodies(
        message.get(
            "payload",
            {}
        )
    )

    candidates = []

    for html_body in bodies["html"]:
        candidates.extend(
            extract_html_links(
                html_body
            )
        )

    url_pattern = re.compile(
        r'https?://[^\s<>"\']+'
    )

    for plain_body in bodies["plain"]:
        candidates.extend(
            url_pattern.findall(
                plain_body
            )
        )

    normalized = []

    for candidate in candidates:

        post_url = normalize_post_url(
            candidate
        )

        if post_url:
            normalized.append(
                post_url
            )

    if not normalized:
        return None

    counts = Counter(
        normalized
    )

    # Python Counter preserves insertion order for ties,
    # so the earliest equally frequent URL wins.

    return counts.most_common(
        1
    )[0][0]


# ---------------------------------------------------------
# Source detection
# ---------------------------------------------------------

def build_searchable_text(message):
    """
    Combines sender, subject, plain text and HTML text into one
    lowercase string for source identification.
    """

    headers = get_headers(
        message
    )

    bodies = extract_message_bodies(
        message.get(
            "payload",
            {}
        )
    )

    combined = [
        headers.get(
            "from",
            ""
        ),
        headers.get(
            "subject",
            ""
        )
    ]

    combined.extend(
        bodies["plain"]
    )

    # HTML is intentionally included because publication names
    # are often present in the HTML header even when the plain
    # MIME part is minimal.

    combined.extend(
        bodies["html"]
    )

    return html.unescape(
        "\n".join(combined)
    ).lower()


def detect_source(message, sources):
    """
    Determines which configured newsletter produced a message.

    Each source provides one or more gmail_match strings.

    Returns
    -------
    dict | None
        Matching source configuration.
    """

    searchable = build_searchable_text(
        message
    )

    for source in sources:

        terms = source.get(
            "gmail_match",
            []
        )

        for term in terms:

            if term.lower() in searchable:
                return source

    return None


# ---------------------------------------------------------
# Article record construction
# ---------------------------------------------------------

def clean_subject(subject):
    """
    Performs minimal normalization of an email subject.

    The scanner deliberately avoids aggressive title rewriting.
    """

    subject = html.unescape(
        subject or ""
    )

    return re.sub(
        r"\s+",
        " ",
        subject
    ).strip()


def build_article_record(
    message,
    source,
    post_url
):
    headers = get_headers(message)

    return {
        "gmail_id": message["id"],
        "source": source["id"],
        "article_id": extract_substack_identity(post_url),
        "title": clean_subject(
            headers.get("subject", "")
        ),
        "post_url": post_url,
        "email_date": extract_email_date(message),
        "status": "discovered"
    }


# ---------------------------------------------------------
# Deduplication and state preservation
# ---------------------------------------------------------

def merge_articles(
    existing_articles,
    discovered_articles
):
    """
    Merges newly discovered records while treating alternate Substack
    URL formats for the same post as one article.
    """

    results = list(
        existing_articles
    )

    existing_keys = {
        article_identity(article)
        for article in results
        if article_identity(article)
    }

    new_count = 0

    for article in discovered_articles:

        key = article_identity(
            article
        )

        if not key:
            continue

        if key in existing_keys:
            continue

        results.append(
            article
        )

        existing_keys.add(
            key
        )

        new_count += 1

    return results, new_count


# ---------------------------------------------------------
# Main scanning operation
# ---------------------------------------------------------

def scan_gmail(
    service, 
    sources,
    query,
    cutoff=None
    ):
    """
    Executes the Gmail discovery stage.

    Returns
    -------
    list
        Newly discovered article records before merging with
        existing state.
    """

    message_ids = search_messages(
        service,
        query
    )

    log_info(
        f"Substack emails found: "
        f"{len(message_ids)}"
    )

    discovered = []

    source_counts = Counter()

    skipped_unknown_source = 0
    skipped_no_url = 0

    for index, message_id in enumerate(
        message_ids,
        start=1
    ):

        try:
            message = get_message(
                service,
                message_id
            )

            if (
                cutoff is not None
                and not message_is_after_cutoff(
                    message,
                    cutoff
                )
            ):
                continue

            source = detect_source(
                message,
                sources
            )

            if source is None:
                skipped_unknown_source += 1
                continue

            post_url = extract_post_url(
                message
            )

            if not post_url:
                skipped_no_url += 1

                log_debug(
                    f"No post URL: "
                    f"{message_id}"
                )

                continue

            article = build_article_record(
                message,
                source,
                post_url
            )

            discovered.append(
                article
            )

            source_counts[
                source["id"]
            ] += 1

        except Exception as error:

            log_error(
                f"Message {message_id}: "
                f"{error}"
            )

        log_debug(
            f"Processed "
            f"{index}/{len(message_ids)}"
        )

    print()

    for source in sources:

        source_id = source["id"]

        print(
            f"  {source['name']:<30} "
            f"{source_counts[source_id]}"
        )

    if DEBUG:
        print()
        log_debug(
            f"Unknown source: "
            f"{skipped_unknown_source}"
        )
        log_debug(
            f"No article URL: "
            f"{skipped_no_url}"
        )

    return discovered


def extract_substack_identity(url):
    """
    Converts different Substack URL formats for the same post into
    a stable article identity.

    Examples
    --------
    https://rianstone.substack.com/p/foo
        -> rianstone::foo

    https://open.substack.com/pub/rianstone/p/foo
        -> rianstone::foo
    """

    if not url:
        return None

    parsed = urlsplit(url)

    host = (parsed.hostname or "").lower()
    path_parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    publication = None
    slug = None

    # publication.substack.com/p/slug
    if host.endswith(".substack.com") and host != "open.substack.com":
        publication = host.split(".")[0]

        if len(path_parts) >= 2 and path_parts[0] == "p":
            slug = path_parts[1]

    # open.substack.com/pub/publication/p/slug
    elif host == "open.substack.com":
        if (
            len(path_parts) >= 4
            and path_parts[0] == "pub"
            and path_parts[2] == "p"
        ):
            publication = path_parts[1]
            slug = path_parts[3]

    if not publication or not slug:
        return None

    return f"{publication}::{slug}"

def article_identity(article):
    """
    Returns the stable identity used for deduplication.
    """

    return extract_substack_identity(
        article.get("post_url")
    )


def run_scan(scheduled=False):
    """
    Runs the Gmail discovery stage.

    Parameters
    ----------
    scheduled : bool
        If True, runs an incremental scan using the previous
        successful scan timestamp as a lower bound, while always
        covering at least the last 24 hours.

    Returns
    -------
    tuple
        (
            merged_articles,
            newly_discovered_articles
        )
    """

    sources = load_sources()

    existing_articles = load_json(
        ARTICLES_FILE,
        default=[]
    )

    existing_ids = {
        article_identity(article)
        for article in existing_articles
        if article_identity(article)
    }

    service = authenticate_gmail()

    if scheduled:

        cutoff = get_scan_cutoff()

        query = build_scheduled_query(
            cutoff
        )

        log_info(
            "Scheduled Gmail scan started."
        )

        log_info(
            f"Scanning from: "
            f"{cutoff.isoformat()}"
        )

    else:

        cutoff = None

        query = GMAIL_QUERY

        log_info(
            "Gmail scan started."
        )

    discovered = scan_gmail(
        service,
        sources,
        query=query,
        cutoff=cutoff
    )

    newly_discovered = [
        article
        for article in discovered
        if article_identity(article)
        not in existing_ids
    ]

    merged, new_count = merge_articles(
        existing_articles,
        discovered
    )

    save_json(
        ARTICLES_FILE,
        merged
    )

    if scheduled:
        save_scan_state()

    print()

    log_ok(
        f"{new_count} new article(s) saved."
    )

    log_info(
        f"Total tracked articles: "
        f"{len(merged)}"
    )

    return (
        merged,
        newly_discovered
    )

def main():
    """
    Command-line entry point.

    Default:
        full/manual scan

    --scheduled:
        incremental scan covering at least the last 24 hours,
        or since the previous successful scheduled scan if older.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Scan Gmail for configured Substack newsletters."
        )
    )

    parser.add_argument(
        "--scheduled",
        action="store_true",
        help=(
            "Run an incremental scheduled scan using "
            "scan_state.json."
        )
    )

    args = parser.parse_args()

    run_scan(
        scheduled=args.scheduled
    )


if __name__ == "__main__":
    main()
