"""What the gate takes out of a registry's answers: versions OSV lists as
malicious, and, when MIN_AGE_DAYS is set, versions published more recently
than that. Pure functions of the response and the feed, so they can be
tested without a network.

Per ecosystem:
  npm   metadata: versions removed, and dist-tags that pointed at one moved
        to the newest remaining; tarballs of a removed version refused.
        Age from the metadata's "time".
  PyPI  simple index: files of a removed version left out; their downloads
        refused. Malicious only (the index Nexus serves carries no dates).
  Go    @v/list: malicious versions left out; .info/.mod/.zip of one
        refused, and of a version younger than MIN_AGE_DAYS by its .info.
"""
import datetime
import json
import re
import urllib.parse

HREF = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>([^<]+)</a>\s*(<br\s*/?>)?', re.I)


def parse_time(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def too_young(published, min_age_days, now):
    t = parse_time(published) if isinstance(published, str) else published
    return bool(min_age_days) and t is not None and now - t < datetime.timedelta(days=min_age_days)


# npm ---------------------------------------------------------------------

def npm_packument(body, feed, min_age_days, now):
    """(new body, removed versions) for a package's metadata document."""
    doc = json.loads(body)
    name = doc.get("name", "")
    times = doc.get("time", {})
    removed = [v for v in list(doc.get("versions", {}))
                if feed.blocked(name, v) or too_young(times.get(v), min_age_days, now)]
    if not removed:
        return body, []
    for v in removed:
        doc["versions"].pop(v, None)
        times.pop(v, None)
    keep = sorted(doc["versions"], key=lambda v: times.get(v, ""))
    tags = doc.get("dist-tags", {})
    for tag, v in list(tags.items()):
        if v in removed:
            if tag == "latest" and keep:
                tags[tag] = keep[-1]
            else:
                del tags[tag]
    return json.dumps(doc).encode(), removed


def npm_tarball(path):
    """(package, version) from .../<package>/-/<basename>-<version>.tgz, or None."""
    m = re.fullmatch(r"(?P<pkg>(?:@[^/]+(?:/|%2[fF]))?[^/@]+)/-/(?P<file>[^/]+)\.tgz", path)
    if not m:
        return None
    pkg = urllib.parse.unquote(m["pkg"])
    base = pkg.rsplit("/", 1)[-1]
    if not m["file"].startswith(base + "-"):
        return None
    return pkg, m["file"][len(base) + 1:]


def npm_package(path):
    """The package a metadata request names (`left-pad`, `@scope/name`)."""
    p = urllib.parse.unquote(path.strip("/"))
    return p if re.fullmatch(r"(@[^/]+/)?[^/@]+", p) else None


# PyPI --------------------------------------------------------------------

def pypi_file(filename):
    """(name, version) from a wheel or sdist file name, or None."""
    f = urllib.parse.unquote(filename)
    if f.endswith(".whl"):
        parts = f[:-4].split("-")
        return (parts[0], parts[1]) if len(parts) >= 5 else None
    for ext in (".tar.gz", ".zip", ".tar.bz2", ".tgz"):
        if f.endswith(ext):
            stem = f[: -len(ext)]
            if "-" in stem:
                name, version = stem.rsplit("-", 1)
                return name, version
    return None


def pypi_simple(body, feed):
    """(new body, removed files) for a simple index page."""
    text = body.decode("utf-8", "replace")
    removed = []

    def keep(m):
        nv = pypi_file(m.group(2).strip())
        if nv and feed.blocked(*nv):
            removed.append(m.group(2).strip())
            return ""
        return m.group(0)

    out = HREF.sub(keep, text)
    return (out.encode(), removed) if removed else (body, [])


# Go ----------------------------------------------------------------------

def go_decode(path):
    """Go's case encoding back to the module path: !a is A."""
    return re.sub(r"!([a-z])", lambda m: m.group(1).upper(), path)


def go_request(path):
    """(module, kind, version) for .../@v/list, @v/<v>.info|mod|zip and
    @latest; None for anything else."""
    m = re.fullmatch(r"(?P<mod>.+)/@v/(?P<rest>.+)", path)
    if m:
        if m["rest"] == "list":
            return go_decode(m["mod"]), "list", None
        v = re.fullmatch(r"(?P<v>.+)\.(?P<k>info|mod|zip)", m["rest"])
        if v:
            return go_decode(m["mod"]), v["k"], urllib.parse.unquote(v["v"])
        return None
    m = re.fullmatch(r"(?P<mod>.+)/@latest", path)
    return (go_decode(m["mod"]), "latest", None) if m else None


def go_list(body, module, feed):
    lines = body.decode().split()
    kept = [v for v in lines if not feed.blocked(module, v)]
    removed = sorted(set(lines) - set(kept))
    return ("\n".join(kept) + ("\n" if kept else "")).encode(), removed
