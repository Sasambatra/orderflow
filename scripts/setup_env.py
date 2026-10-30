"""Generate independent development secrets without overwriting local settings."""

import os
import secrets
from pathlib import Path


def main():
    path = Path(".env")
    if path.exists():
        print(".env already exists; preserved")
        return
    password = secrets.token_hex(24)
    content = Path(".env.example").read_text().replace("GENERATE_DB_PASSWORD", password)
    content = content.replace("GENERATE_JWT_SECRET", secrets.token_urlsafe(48))
    content = content.replace("GENERATE_WEBHOOK_SECRET", secrets.token_urlsafe(48))
    with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(content)
    print("Generated .env with independent random credentials")


if __name__ == "__main__":
    main()
