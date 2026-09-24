import requests


PDF_URL = (
    "https://rianstone.substack.com/"
    "api/v1/post/pdf?postId=216928353"
)


def main():
    response = requests.get(
        PDF_URL,
        timeout=30,
        allow_redirects=True,
    )

    print(f"Status .......... {response.status_code}")
    print(f"Content-Type .... {response.headers.get('Content-Type')}")
    print(f"Final URL ....... {response.url}")
    print(f"Size ............ {len(response.content)} bytes")
    print(f"First bytes ..... {response.content[:10]!r}")

    if (
        response.status_code == 200
        and response.content.startswith(b"%PDF-")
    ):
        print()
        print("[OK] PDF is directly downloadable without Selenium cookies.")
    else:
        print()
        print("[INFO] Direct HTTP download requires further investigation.")


if __name__ == "__main__":
    main()