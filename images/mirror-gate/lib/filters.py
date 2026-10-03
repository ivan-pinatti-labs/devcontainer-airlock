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

# The links of a simple index page, found in steps rather than with one
# regular expression: <a\s[^>]*href="([^"]+)"[^>]*>([^<]+)</a>\s*(<br\s*/?>)?
# matches the same, but backtracks in polynomial time on a page built to
# make it. tests/test_mirror_gate_lib.py holds the two to the same answers.
_LINK_START = re.compile(r"<a\s", re.I)
_HREF = re.compile(r'href="', re.I)
_TAIL = re.compile(r"\s*(?:<br\s*/?>)?", re.I)


def _link_end(text, h):
    """(end, link text) of a link whose href=" is at h, or None."""
    q = text.find('"', h + 6)
    if q <= h + 6:
        return None
    c = text.find(">", q + 1)
    if c < 0:
        return None
    lt = text.find("<", c + 1)
    if lt <= c + 1 or text[lt:lt + 4].lower() != "</a>":
        return None
    return _TAIL.match(text, lt + 4).end(), text[c + 1:lt]


def links(text):
    """(start, end, link text) of each <a ... href="...">text</a> in text,
    with the whitespace and <br> after it, left to right."""
    n, i = len(text), 0
    while m := _LINK_START.search(text, i):
        p = m.start()
        f1 = text.find(">", p + 3)
        limit = n if f1 < 0 else f1
        # The last href=" before the tag's first > is tried first, as the
        # expression's greedy [^>]* would.
        candidates = [h.start() for h in _HREF.finditer(text, p + 3, limit)]
        for h in reversed(candidates):
            found = _link_end(text, h)
            if found:
                yield p, found[0], found[1]
                i = found[0]
                break
        else:
            # A link starting further on, before that first >, would try a
            # subset of the same href="s: none can match either.
            if f1 < 0:
                return
            i = f1 + 1


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


def _npm_name(pkg):
    """Whether pkg is a package name as a tarball path gives it: name, or
    @scope/name with the / possibly encoded as %2f. The same test as the
    expression (?:@[^/]+(?:/|%2[fF]))?[^/@]+, which backtracks in
    polynomial time on a scope holding many %2f."""
    if not pkg.startswith("@"):
        return pkg != "" and "/" not in pkg and "@" not in pkg
    rest = pkg[1:]
    if "/" in rest:
        scope, _, name = rest.partition("/")
        return scope != "" and name != "" and "/" not in name and "@" not in name
    # An encoded / with a scope before it and a name after it; the name
    # holds no @, so the %2f ends after the last @.
    i = max(rest.rfind("@") - 2, 1)
    while (i := rest.find("%2", i)) >= 0:
        if rest[i + 2:i + 3] in ("f", "F") and i + 3 < len(rest):
            return True
        i += 1
    return False


def npm_tarball(path):
    """(package, version) from .../<package>/-/<basename>-<version>.tgz, or None."""
    if not path.endswith(".tgz"):
        return None
    parts = path[:-4].split("/")
    if len(parts) < 3 or parts[-2] != "-" or parts[-1] == "" or not _npm_name("/".join(parts[:-2])):
        return None
    pkg, file = urllib.parse.unquote("/".join(parts[:-2])), parts[-1]
    base = pkg.rsplit("/", 1)[-1]
    if not file.startswith(base + "-"):
        return None
    return pkg, file[len(base) + 1:]


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

    def keep(link, name):
        nv = pypi_file(name.strip())
        if nv and feed.blocked(*nv):
            removed.append(name.strip())
            return ""
        return link

    out, last = [], 0
    for start, end, name in links(text):
        out.append(text[last:start])
        out.append(keep(text[start:end], name))
        last = end
    out.append(text[last:])
    return ("".join(out).encode(), removed) if removed else (body, [])


# Go ----------------------------------------------------------------------

def go_decode(path):
    """Go's case encoding back to the module path: !a is A."""
    return re.sub(r"!([a-z])", lambda m: m.group(1).upper(), path)


def go_request(path):
    """(module, kind, version) for .../@v/list, @v/<v>.info|mod|zip and
    @latest; None for anything else."""
    # The last /@v/ with something before and after it, as the expression
    # (.+)/@v/(.+) would split it (. stops at a newline), without its
    # polynomial backtracking.
    i = -1 if "\n" in path else path.rfind("/@v/", 1, len(path) - 1)
    if i > 0:
        mod, rest = path[:i], path[i + 4:]
        if rest == "list":
            return go_decode(mod), "list", None
        v = re.fullmatch(r"(?P<v>.+)\.(?P<k>info|mod|zip)", rest)
        if v:
            return go_decode(mod), v["k"], urllib.parse.unquote(v["v"])
        return None
    m = re.fullmatch(r"(?P<mod>.+)/@latest", path)
    return (go_decode(m["mod"]), "latest", None) if m else None


def go_list(body, module, feed):
    lines = body.decode().split()
    kept = [v for v in lines if not feed.blocked(module, v)]
    removed = sorted(set(lines) - set(kept))
    return ("\n".join(kept) + ("\n" if kept else "")).encode(), removed
