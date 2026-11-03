"""应用版本和更新地址。"""

from __future__ import annotations

import re
import os

APP_VERSION = "1.5.21"
# A public project identifier keeps update discovery independent of account names.
GITHUB_REPO = os.environ.get("SHEETS_POST_FILTER_UPDATE_REPO", "").strip()
_REPO_API = f"https://api.github.com/repos/{GITHUB_REPO}" if GITHUB_REPO else "https://api.github.com/repositories/1396680321"
UPDATE_API_URL = _REPO_API + "/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest" if GITHUB_REPO else UPDATE_API_URL


def version_tuple(value: str) -> tuple[int, ...]:
    parts = [int(item) for item in re.findall(r"\d+", str(value or ""))[:4]]
    parts = parts or [0]
    return tuple((parts + [0, 0, 0, 0])[:4])
