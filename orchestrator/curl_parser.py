import shlex
from urllib.parse import urlparse


class CurlParseError(Exception):
    pass


def parse_curl(curl_string):
    """Parse a curl command string into requests-compatible kwargs.

    Returns dict with keys: method, url, headers, data, cookies.
    Raises CurlParseError if unparseable or targets wrong host.
    """
    curl_string = curl_string.strip()
    if curl_string.startswith("curl "):
        curl_string = curl_string[5:]

    try:
        tokens = shlex.split(curl_string)
    except ValueError as e:
        raise CurlParseError(f"Failed to parse curl command: {e}")

    method = "GET"
    url = None
    headers = {}
    data = None
    cookies = {}

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
        elif token in ("-H", "--header") and i + 1 < len(tokens):
            header = tokens[i + 1]
            if ":" in header:
                key, val = header.split(":", 1)
                headers[key.strip()] = val.strip()
            i += 2
        elif token in ("-d", "--data", "--data-raw") and i + 1 < len(tokens):
            data = tokens[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
        elif token in ("-b", "--cookie") and i + 1 < len(tokens):
            cookie_str = tokens[i + 1]
            for pair in cookie_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    cookies[k.strip()] = v.strip()
            i += 2
        elif not token.startswith("-"):
            if url is None:
                url = token
            i += 1
        else:
            # Unknown flag — skip
            i += 1

    if not url:
        raise CurlParseError("No URL found in curl command")

    # Validate URL targets localhost:5050 only
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if hostname not in ("localhost", "127.0.0.1") or port != 5050:
        raise CurlParseError(
            f"URL must target localhost:5050, got {hostname}:{port}"
        )

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "data": data,
        "cookies": cookies,
    }
