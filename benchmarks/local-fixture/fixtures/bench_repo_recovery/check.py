from pathlib import Path

settings = dict(
    line.split("=", 1)
    for line in Path("config.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
)

assert settings.get("mode") == "release"
assert settings.get("retries") == "3"
