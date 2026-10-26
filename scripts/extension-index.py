#!/usr/bin/env python3
"""Write the release index Renovate reads for the workbench's VS Code extensions.

Reads images/workbench/vscode/extensions.txt and asks the Visual Studio
Marketplace for each extension's releases, then writes one JSON file per
extension, in the shape Renovate's custom datasources take:

  {"releases": [{"version": "1.2.3", "releaseTimestamp": "..."}, ...],
    "homepage": "...", "changelogUrl": "..."}

Only stable releases (not pre-releases) built for linux-x64 or for every
platform are listed, each with the date the Marketplace published it. Nothing
here judges age: Renovate's minimumReleaseAge holds a release back until it is
seven days old, the same rule as every other pin (docs/LAYERS.md,
"Extensions").

Renovate has no Marketplace datasource, and its custom datasources can only
GET a URL, while the Marketplace's release query is a POST. So the Extension
Index workflow runs this every day and publishes the files to the
`extension-index` branch, where Renovate reads them (.github/renovate.json5).

Usage:
  scripts/extension-index.py <output directory>

Runs anywhere Python 3 and network access to the Marketplace are, which in
the workbench means L2 with the egress proxy:
  l2 --net -- scripts/extension-index.py /tmp/index

Exit status codes:
  0  the index was written
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
ITEM = "https://marketplace.visualstudio.com/items"
PLATFORMS = (None, "linux-x64")
PIN = re.compile(
    r"^(?P<id>[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*)@(?P<version>\S+)$", re.IGNORECASE
)


def read_pins():
    """The extension identifiers, as written, since Renovate names each file
    after the identifier it read from the same line."""
    ids = []
    for line in PINS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN.match(line)
        if not m:
            print(f"extension-index: cannot read '{line}' in {PINS}", file=sys.stderr)
            sys.exit(2)
        ids.append(m["id"])
    return ids


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
        print(f"extension-index: {ext_id} is not on the Marketplace", file=sys.stderr)
        sys.exit(2)
    return results[0]["versions"]


def is_pre_release(v):
    return any(
        p.get("key") == "Microsoft.VisualStudio.Code.PreRelease"
        and p.get("value") == "true"
        for p in v.get("properties", [])
    )


def index(ext_id):
    """The stable releases the workbench could install, newest first. A
    version published for more than one matching platform is listed once,
    dated by its latest upload, so its age is never overstated."""
    dates = {}
    for v in releases(ext_id):
        if v.get("targetPlatform") not in PLATFORMS or is_pre_release(v):
            continue
        released = datetime.datetime.fromisoformat(v["lastUpdated"])
        dates[v["version"]] = max(released, dates.get(v["version"], released))
    ordered = sorted(dates.items(), key=lambda item: item[1], reverse=True)
    return {
        "releases": [
            {"version": version, "releaseTimestamp": released.isoformat()}
            for version, released in ordered
        ],
        "homepage": f"{ITEM}?itemName={ext_id}",
        "changelogUrl": f"{ITEM}/{ext_id}/changelog",
    }


def main(argv):
    if len(argv) != 1 or argv[0].startswith("-"):
        print(
            __doc__.split("Usage:")[1].split("Runs anywhere")[0].rstrip(),
            file=sys.stderr,
        )
        return 2
    out = pathlib.Path(argv[0])
    try:
        indexes = {ext_id: index(ext_id) for ext_id in read_pins()}
    except OSError as e:
        print(
            f"extension-index: the Marketplace could not be read: {e}", file=sys.stderr
        )
        return 2
    out.mkdir(parents=True, exist_ok=True)
    for ext_id, data in indexes.items():
        (out / f"{ext_id}.json").write_text(json.dumps(data, indent=2) + "\n")
        print(f"{ext_id}: {len(data['releases'])} stable release(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
