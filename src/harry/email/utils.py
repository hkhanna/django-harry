import json


def trim_string(field: str) -> str:
    """Remove superfluous linebreaks and whitespace"""
    lines = field.splitlines()
    sanitized_lines = []
    for line in lines:
        sanitized_line = line.strip()
        if sanitized_line:  # Remove blank lines
            sanitized_lines.append(line.strip())
    sanitized = " ".join(sanitized_lines).strip()
    return sanitized


def validate_request_body_json(
    *, body: str, required_keys: list | None = None
) -> list | dict:
    """Validate that the request body is JSON and return the parsed JSON."""
    try:
        body_json = json.loads(body)
    except json.decoder.JSONDecodeError:
        raise ValueError("Invalid payload")

    # Ensure all required keys
    if required_keys is None:
        required_keys = []

    for key in required_keys:
        try:
            body_json[key]
        except KeyError:
            raise ValueError("Invalid payload")

    return body_json
