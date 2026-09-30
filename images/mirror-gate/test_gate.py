"""Filter cases for the mirror gate, run when its image is built, so a change
that lets a malicious version through fails the build."""
# cspell:words pytz fooo
import datetime
import json
import sys

sys.path.insert(0, "/usr/local/lib/mirror-gate")
import filters  # noqa: E402
import osvdb  # noqa: E402

NOW = datetime.datetime(2026, 9, 29, tzinfo=datetime.timezone.utc)
bad = []


def check(what, got, want):
    if got != want:
        bad.append(f"{what}: got {got!r}, want {want!r}")


# Feeds built the way sync() builds them, from MAL- entries.
npm = osvdb.Feed("npm")
npm._add("MAL-1", json.dumps({"affected": [{"package": {"ecosystem": "npm", "name": "evil-pkg"},
                                            "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}]}]}]}))
npm._add("MAL-2", json.dumps({"affected": [{"package": {"ecosystem": "npm", "name": "left-pad"},
                                            "versions": ["1.3.1"]}]}))
npm._add("MAL-3", json.dumps({"withdrawn": "2026-01-01", "affected": [
    {"package": {"ecosystem": "npm", "name": "was-bad"}, "ranges": [{"events": [{"introduced": "0"}]}]}]}))
npm._index()
pypi = osvdb.Feed("PyPI")
pypi._add("MAL-4", json.dumps({"affected": [{"package": {"ecosystem": "PyPI", "name": "Evil_Pkg"},
                                              "versions": ["1.0"]}]}))
pypi._index()
go = osvdb.Feed("Go")
go._add("MAL-5", json.dumps({"affected": [{"package": {"ecosystem": "Go", "name": "github.com/Evil/mod"},
                                            "versions": ["v1.2.3"]}]}))
go._index()

check("npm whole package", npm.blocked("evil-pkg", "9.9.9"), True)
check("npm one version", npm.blocked("left-pad", "1.3.1"), True)
check("npm other version", npm.blocked("left-pad", "1.3.0"), False)
check("npm withdrawn entry", npm.blocked("was-bad", "1.0.0"), False)
check("pypi normalized name", pypi.blocked("evil-pkg", "1.0"), True)
check("pypi other version", pypi.blocked("evil_pkg", "1.1"), False)

doc = {"name": "left-pad", "dist-tags": {"latest": "1.3.1", "beta": "1.3.1"},
        "versions": {"1.3.0": {}, "1.3.1": {}, "1.4.0": {}},
        "time": {"1.3.0": "2020-01-01T00:00:00Z", "1.3.1": "2021-01-01T00:00:00Z",
                "1.4.0": "2026-09-27T00:00:00Z"}}
body, removed = filters.npm_packument(json.dumps(doc).encode(), npm, 0, NOW)
out = json.loads(body)
check("npm packument removes malicious", sorted(removed), ["1.3.1"])
check("npm latest moves to newest left", out["dist-tags"]["latest"], "1.4.0")
check("npm other tag on it dropped", "beta" in out["dist-tags"], False)
body, removed = filters.npm_packument(json.dumps(doc).encode(), npm, 7, NOW)
check("npm min age removes young", sorted(removed), ["1.3.1", "1.4.0"])
check("npm latest after age", json.loads(body)["dist-tags"]["latest"], "1.3.0")

check("npm tarball", filters.npm_tarball("left-pad/-/left-pad-1.3.1.tgz"), ("left-pad", "1.3.1"))
check("npm scoped tarball", filters.npm_tarball("@scope/pkg/-/pkg-2.0.0-rc.1.tgz"), ("@scope/pkg", "2.0.0-rc.1"))
check("npm scoped encoded", filters.npm_tarball("@scope%2fpkg/-/pkg-2.0.0.tgz"), ("@scope/pkg", "2.0.0"))
check("npm not a tarball", filters.npm_tarball("left-pad"), None)
check("npm package", filters.npm_package("@scope%2fpkg"), "@scope/pkg")

check("wheel", filters.pypi_file("Evil_Pkg-1.0-py3-none-any.whl"), ("Evil_Pkg", "1.0"))
check("sdist", filters.pypi_file("evil-pkg-1.0.tar.gz"), ("evil-pkg", "1.0"))
html = ('<html><body>\n<a href="../../packages/a/evil_pkg-1.0-py3-none-any.whl#sha256=1">'
        'evil_pkg-1.0-py3-none-any.whl</a><br/>\n<a href="../../packages/b/evil_pkg-1.1.tar.gz#sha256=2">'
        'evil_pkg-1.1.tar.gz</a><br/>\n</body></html>').encode()
body, removed = filters.pypi_simple(html, pypi)
check("pypi index drops malicious file", removed, ["evil_pkg-1.0-py3-none-any.whl"])
check("pypi index keeps the rest", b"evil_pkg-1.1.tar.gz" in body and b"1.0-py3" not in body, True)

check("go decode", filters.go_decode("github.com/!evil/mod"), "github.com/Evil/mod")
check("go list request", filters.go_request("github.com/!evil/mod/@v/list"), ("github.com/Evil/mod", "list", None))
check("go zip request", filters.go_request("github.com/!evil/mod/@v/v1.2.3.zip"), ("github.com/Evil/mod", "zip", "v1.2.3"))
check("go latest", filters.go_request("github.com/!evil/mod/@latest"), ("github.com/Evil/mod", "latest", None))
body, removed = filters.go_list(b"v1.2.2\nv1.2.3\nv1.3.0\n", "github.com/Evil/mod", go)
check("go list drops malicious", (body, removed), (b"v1.2.2\nv1.3.0\n", ["v1.2.3"]))
check("too young", filters.too_young("2026-09-28T00:00:00Z", 7, NOW), True)
check("old enough", filters.too_young("2026-09-01T00:00:00Z", 7, NOW), False)
check("age off", filters.too_young("2026-09-28T00:00:00Z", 0, NOW), False)

for b in bad:
    print("WRONG", b)
print(f"{'FAILED' if bad else 'all'} gate filter cases {'' if bad else 'as expected'}".strip())
sys.exit(1 if bad else 0)
