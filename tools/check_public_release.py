"""Fail-closed public source / artifact check. Never print matched content."""
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {"config.json", "cloudflare.json", "sync_state.json", "desktop_error.log",
                 ".cache-publish-secret.txt", "_test_publish_body.json"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "build", "dist", "release", "private-backup", "public-export"}
TEXT_TYPES = {".py", ".js", ".mjs", ".html", ".css", ".json", ".toml", ".yml", ".yaml", ".md", ".txt", ".gs", ".bat", ".ps1", ".iss", ".lock", ".example"}
RULES = {
    "private key": re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),
    "token pattern": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})"),
    "service account email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com"),
    "Google deployment": re.compile(r"https://script\.google\.com/macros/s/[\w-]{30,}/exec"),
    "embedded Google id": re.compile(r'''["'](1[A-Za-z0-9_-]{19,})["']'''),
    "Google document id": re.compile(r"(?:spreadsheets/d/|drive\.google\.com/file/d/|[?&]id=)([\w-]{20,})"),
    "credential value": re.compile(r'''["']?(?:api_token|access_password|cf_publish_secret|private_key)["']?\s*[:=]\s*["']([^"'\r\n]{8,})["']''', re.I),
}


def example(value):
    lowered=value.lower()
    return any(word in lowered for word in ("example", "fixture", "test", "示例", "xxxxx")) or len(set(value)) <= 2


def inspect(path, relative):
    errors=[]
    if (path.name.startswith(".env") and path.name!=".env.example") or path.suffix.lower() in {".pem", ".key", ".pfx", ".p12"}:
        return [(relative,0,"private environment or certificate")]
    if path.name in PRIVATE_NAMES or path.name.startswith("credentials") and path.suffix==".json":
        return [(relative,0,"private runtime file")]
    if path.suffix.lower() not in TEXT_TYPES and path.name not in {".env.example", "_headers", "pre-push"}:
        return errors
    for number,line in enumerate(path.read_text(encoding="utf-8-sig",errors="replace").splitlines(),1):
        for name,pattern in RULES.items():
            for match in pattern.finditer(line):
                value=match.group(1) if match.lastindex else match.group(0)
                if name=="credential value" and value in {"api_token", "access_password", "cf_publish_secret", "private_key"}:
                    continue  # Field-name mapping, not a credential value.
                if not example(value):errors.append((relative,number,name))
    return errors


def main():
    marker=ROOT/"PUBLIC-SOURCE.json"
    if not marker.exists():
        print("BLOCKED: use the reviewed history-free public export, never the private working repository.")
        return 1
    if json.loads(marker.read_text(encoding="utf-8")).get("sanitized") is not True:
        print("BLOCKED: public export is not marked as reviewed.");return 1
    findings=[]
    for path in ROOT.rglob("*"):
        rel=path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):continue
        if path.is_symlink():findings.append((rel.as_posix(),0,"symlink"));continue
        if path.is_file():findings.extend(inspect(path,rel.as_posix()))
    for name,line,rule in findings:
        print(f"BLOCKED: {name}:{line}: {rule}")
    if not findings:print("Public release source check passed; private data and old Git history must never be copied into this directory.")
    return bool(findings)


if __name__=="__main__":sys.exit(main())
