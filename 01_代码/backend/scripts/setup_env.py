"""Create a local .env from its template, without exposing or replacing secrets."""
import argparse
from pathlib import Path
import re
import secrets


def create_env(backend_dir: Path, mongodb_uri: str | None = None, database: str | None = None) -> bool:
    destination = backend_dir / ".env"
    if destination.exists():
        return False
    if mongodb_uri is not None and (
        not mongodb_uri.startswith(("mongodb://", "mongodb+srv://")) or any(c in mongodb_uri for c in "\r\n")
    ):
        raise ValueError("MONGODB_URI must be a MongoDB connection URI without line breaks")
    if database is not None and (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", database) or database in {"admin", "local", "config"}
    ):
        raise ValueError("Use a non-system database name with 1-63 letters, digits, underscores or hyphens")
    replacements = {"SECRET_KEY": secrets.token_hex(48)}
    if mongodb_uri is not None:
        # JSON-style quoting is understood by python-dotenv and protects # in URI values.
        import json
        replacements["MONGODB_URI"] = json.dumps(mongodb_uri)
    if database is not None:
        replacements["MONGODB_DATABASE"] = database
    template = (backend_dir / ".env.example").read_text(encoding="utf-8")
    lines = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={replacements[key]}" if key in replacements else line)
    try:
        # Exclusive creation also prevents an accidental overwrite in a startup race.
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
    except FileExistsError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongodb-uri", help="Optional MongoDB connection URI for the new file")
    parser.add_argument("--database", help="Optional separate database name for the new file")
    args = parser.parse_args()
    try:
        created = create_env(Path(__file__).resolve().parents[1], args.mongodb_uri, args.database)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Configuration setup failed: {exc}\n")
    print("Created backend/.env with a random signing key. No secret was printed."
          if created else "backend/.env already exists; its configuration and signing key were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
