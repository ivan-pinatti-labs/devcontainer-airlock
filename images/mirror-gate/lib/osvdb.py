"""Malicious package versions, from OSV's MAL- entries (the OpenSSF
malicious-packages feed), per ecosystem.

Only MAL- entries are read. Vulnerabilities (CVE, GHSA and the rest) never
block anything here: they are reported elsewhere, not enforced.

The first sync downloads the ecosystem's all.zip once; later syncs read
modified_id.csv and fetch only the MAL- entries changed since, one JSON
each. Everything goes through the process's HTTPS_PROXY, the egress proxy.
State is kept in CACHE/<ecosystem>.json, so a restart does not start over.
"""
import csv
import io
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile

BASE = os.environ.get("OSV_BASE", "https://storage.googleapis.com/osv-vulnerabilities")
CACHE = pathlib.Path(os.environ.get("OSV_CACHE", "/var/lib/mirror-gate/osv"))
ECOSYSTEMS = ("npm", "PyPI", "Go")


def log(msg):
    print(f"osv: {msg}", flush=True)


def normalize(ecosystem, name):
    """The form names are compared in: PEP 503 for PyPI, as given otherwise."""
    if ecosystem == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name


def affected(entry, ecosystem):
    """(name, versions or None) for each package the entry marks; None means
    every version, which is how most MAL- entries read (introduced 0, no fix)."""
    for a in entry.get("affected", []):
        pkg = a.get("package", {})
        if pkg.get("ecosystem") != ecosystem or not pkg.get("name"):
            continue
        versions = set(a.get("versions") or [])
        whole = any(
            any(e.get("introduced") == "0" for e in r.get("events", []))
            and not any("fixed" in e or "last_affected" in e for e in r.get("events", []))
            for r in a.get("ranges", []))
        yield normalize(ecosystem, pkg["name"]), (None if whole or not versions else versions)


class Feed:
    """One ecosystem's malicious names and versions, safe to read while it
    refreshes."""

    def __init__(self, ecosystem):
        self.ecosystem = ecosystem
        self.whole = set()
        self.versions = {}
        self.entries = {}
        self.synced = None
        self.lock = threading.Lock()

    def blocked(self, name, version=None):
        name = normalize(self.ecosystem, name)
        with self.lock:
            if name in self.whole:
                return True
            return version is not None and version in self.versions.get(name, ())

    def _index(self):
        whole, versions = set(), {}
        for marks in self.entries.values():
            for name, vs in marks:
                if vs is None:
                    whole.add(name)
                else:
                    versions.setdefault(name, set()).update(vs)
        with self.lock:
            self.whole, self.versions = whole, versions

    def _add(self, entry_id, data):
        entry = json.loads(data)
        if entry.get("withdrawn"):
            self.entries.pop(entry_id, None)
        else:
            self.entries[entry_id] = [(n, sorted(v) if v else None) for n, v in affected(entry, self.ecosystem)]

    def _state(self):
        return CACHE / f"{self.ecosystem}.json"

    def load(self):
        try:
            s = json.loads(self._state().read_text())
        except (OSError, ValueError):
            return False
        self.entries = {k: [(n, set(v) if v else None) for n, v in marks] for k, marks in s["entries"].items()}
        self.synced = s["synced"]
        self._index()
        log(f"{self.ecosystem}: {len(self.entries)} entries from the cache, synced {self.synced}")
        return True

    def save(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = self._state().with_suffix(".tmp")
        tmp.write_text(json.dumps({"synced": self.synced, "entries": {
            k: [(n, sorted(v) if v else None) for n, v in marks] for k, marks in self.entries.items()}}))
        tmp.replace(self._state())

    def sync(self):
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self.synced is None:
            self._full()
        else:
            self._incremental()
        self.synced = started
        self._index()
        self.save()
        log(f"{self.ecosystem}: {len(self.entries)} malicious entries "
            f"({len(self.whole)} whole packages, {sum(map(len, self.versions.values()))} single versions)")

    def _full(self):
        tmp = CACHE / f"{self.ecosystem}.zip"
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{BASE}/{self.ecosystem}/all.zip", timeout=600) as r, open(tmp, "wb") as f:  # noqa: S310
            while chunk := r.read(1 << 20):
                f.write(chunk)
        self.entries = {}
        with zipfile.ZipFile(tmp) as z:
            for n in z.namelist():
                if n.startswith("MAL-") and n.endswith(".json"):
                    self._add(n[:-5], z.read(n))
        tmp.unlink()

    def _incremental(self):
        with urllib.request.urlopen(f"{BASE}/{self.ecosystem}/modified_id.csv", timeout=120) as r:  # noqa: S310
            rows = csv.reader(io.TextIOWrapper(r, encoding="utf-8"))
            changed = []
            for row in rows:
                # Newest first: stop at the first row older than the last sync.
                if len(row) < 2 or row[0] < self.synced:
                    break
                if row[1].startswith("MAL-"):
                    changed.append(row[1])
        for entry_id in changed:
            try:
                with urllib.request.urlopen(f"{BASE}/{self.ecosystem}/{entry_id}.json", timeout=60) as r:  # noqa: S310
                    self._add(entry_id, r.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.entries.pop(entry_id, None)
                else:
                    raise


FEEDS = {e: Feed(e) for e in ECOSYSTEMS}


def refresh_forever(every):
    """Load the cache, then sync every `every` seconds. A failed sync keeps
    the last good data and says so; with none at all, nothing is blocked
    and the gate's status says the filter is not armed."""
    for f in FEEDS.values():
        f.load()
    while True:
        for f in FEEDS.values():
            try:
                f.sync()
            except Exception as e:  # noqa: BLE001  a feed must never stop the others
                log(f"WARNING {f.ecosystem}: sync failed ({e}); keeping the data from {f.synced or 'nowhere'}")
        time.sleep(every)
