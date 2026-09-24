from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import warnings

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

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]

# warnings.filterwarnings(
#     "ignore",
#     category=FutureWarning
# )

# warnings.filterwarnings(
#     "ignore",
#     category=DeprecationWarning
# )

def authenticate_gmail():
    """
    Authenticates the local application with Gmail.

    Existing OAuth credentials are reused from token.json.

    If no valid token exists, the function starts Google's
    local OAuth authorization flow and stores the resulting
    token for future executions.

    Returns
    -------
    googleapiclient.discovery.Resource
        Authenticated Gmail API service.
    """

    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES
        )

    if not credentials or not credentials.valid:

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            credentials.refresh(
                Request()
            )

        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE,
                SCOPES
            )

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
        credentials=credentials
    )


def main():
    service = authenticate_gmail()

    profile = (
        service
        .users()
        .getProfile(
            userId="me"
        )
        .execute()
    )

    print("=== GMAIL API TEST ===")
    print(
        f"Email ............ {profile['emailAddress']}"
    )
    print(
        f"Messages ......... {profile['messagesTotal']}"
    )
    print(
        f"Threads .......... {profile['threadsTotal']}"
    )

    print()
    print("[OK] Gmail API authentication successful.")


if __name__ == "__main__":
    main()