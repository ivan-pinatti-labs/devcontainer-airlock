#!/usr/bin/env python3
"""Check, and on request move, the VS Code extension pins of the workbench.

Reads images/workbench/vscode/extensions.txt and asks the Visual Studio
Marketplace for each extension's releases. An update is eligible when it is a
stable release (not a pre-release), built for linux-x64 or for every
platform, and at least seven days old: a malicious update to a trusted
extension is usually found and pulled within days, which is the same reason
the coding agent CLIs wait seven days (docs/LAYERS.md, "Extensions").

There is no bot for this on purpose. Renovate has no Marketplace datasource,
and extensions are the one dependency here that runs with the editor's full
permissions, so a person reads each bump. The weekly workflow runs --check
and keeps one issue open while updates are due.

Usage:
  scripts/extension-pins.py --check    list eligible updates
  scripts/extension-pins.py --update   rewrite extensions.txt to them

Runs anywhere Python 3 and network access to the Marketplace are, which in
the workbench means L2 with the egress proxy:
  l2 --net -- scripts/extension-pins.py --check

Exit status codes:
  0  nothing due (--check), or the file was rewritten (--update)
  1  updates are due (--check)
  2  usage error, or the Marketplace could not be read
"""

import datetime
import json
import pathlib
import re
import sys
import urllib.request

PINS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "images/workbench/vscode/extensions.txt"
)
QUERY = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
MIN_AGE = datetime.timedelta(days=7)
PLATFORMS = (None, "linux-x64")
PIN = re.compile(
    r"^(?P<id>[a-z0-9][a-z0-9.-]*\.[a-z0-9][a-z0-9-]*)@(?P<version>\S+)$", re.IGNORECASE
)


def read_pins():
    pins = {}
    for line in PINS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN.match(line)
        if not m:
            print(f"extension-pins: cannot read '{line}' in {PINS}", file=sys.stderr)
            sys.exit(2)
        pins[m["id"].lower()] = m["version"]
    return pins


def releases(ext_id):
    body = {
        "filters": [{"criteria": [{"filterType": 7, "value": ext_id}]}],
        # IncludeVersions | IncludeVersionProperties | IncludeLatestVersionOnly off
        "flags": 0x1 | 0x10 | 0x80,
    }
    req = urllib.request.Request(
        QUERY,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json;api-version=7.2-preview.1",
        },
    )
    # S310: the URL is the constant QUERY above, always https.
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        results = json.load(r)["results"][0]["extensions"]
    if not results:
        print(f"extension-pins: {ext_id} is not on the Marketplace", file=sys.stderr)
        sys.exit(2)
    return results[0]["versions"]


def is_pre_release(v):
    return any(
        p.get("key") == "Microsoft.VisualStudio.Code.PreRelease"
        and p.get("value") == "true"
        for p in v.get("properties", [])
    )


def eligible(ext_id, pinned, now):
    """The newest stable release old enough to take, if it is newer than the
    pin. The Marketplace lists releases newest first, so reaching the pinned
    version before finding one means nothing newer qualifies, and a pin set by
    hand to something newer than any eligible release is never moved back."""
    for v in releases(ext_id):
        if v["version"] == pinned:
            return None, None
        if v.get("targetPlatform") not in PLATFORMS or is_pre_release(v):
            continue
        released = datetime.datetime.fromisoformat(v["lastUpdated"])
        if now - released >= MIN_AGE:
            return v["version"], released.date().isoformat()
    return None, None


def main(argv):
    if argv not in (["--check"], ["--update"]):
        print(
            __doc__.split("Usage:")[1].split("Runs anywhere")[0].rstrip(),
            file=sys.stderr,
        )
        return 2
    now = datetime.datetime.now(datetime.UTC)
    pins = read_pins()
    due = {}
    try:
        for ext_id, pinned in pins.items():
            version, released = eligible(ext_id, pinned, now)
            if version:
                due[ext_id] = (pinned, version, released)
    except OSError as e:
        print(
            f"extension-pins: the Marketplace could not be read: {e}", file=sys.stderr
        )
        return 2
    for ext_id, (pinned, version, released) in sorted(due.items()):
        print(f"{ext_id}: {pinned} -> {version} (released {released})")
    if argv == ["--check"]:
        if not due:
            print("extension-pins: every pin is current")
        return 1 if due else 0
    text = PINS.read_text()
    for ext_id, (pinned, version, _) in due.items():
        text = re.sub(
            rf"(?im)^({re.escape(ext_id)})@{re.escape(pinned)}$", rf"\1@{version}", text
        )
    PINS.write_text(text)
    print(
        f"extension-pins: rewrote {len(due)} pin(s) in {PINS.relative_to(PINS.parents[3])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
