"""Tests for images/egress-proxy/bin/egress-refresh.

Every path the script reads or writes is pointed at a scratch directory, and
the providers' APIs are replaced by canned answers, so nothing here reaches
the network.
"""

from __future__ import annotations

import io
import json

import pytest
from conftest import REPO_ROOT, load

SCRIPT = "images/egress-proxy/bin/egress-refresh"


@pytest.fixture
def er(tmp_path, monkeypatch):
    module = load(SCRIPT, "egress_refresh")
    sets = tmp_path / "sets"
    sets.mkdir()
    monkeypatch.setattr(module, "SETS_DIR", sets)
    monkeypatch.setattr(module, "WORKSPACES", tmp_path / "workspaces")
    monkeypatch.setattr(module, "OUT", tmp_path / "out")
    monkeypatch.setattr(module, "CACHE", tmp_path / "cache")
    return module


def write_set(er, name, text):
    (er.SETS_DIR / f"{name}.toml").write_text(text)


def register(er, name, text):
    er.WORKSPACES.mkdir(exist_ok=True)
    (er.WORKSPACES / f"{name}.conf").write_text(text)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_json_reads_the_answer(er, monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen["url"], seen["agent"], seen["timeout"] = (
            req.full_url,
            req.get_header("User-agent"),
            timeout,
        )
        return FakeResponse(b'{"a": 1}')

    monkeypatch.setattr(er.urllib.request, "urlopen", urlopen)
    assert er.fetch_json("https://example.test/x") == {"a": 1}
    assert seen == {
        "url": "https://example.test/x",
        "agent": "egress-refresh",
        "timeout": 30,
    }


def test_github_meta_picks_keys_and_categories(er, monkeypatch):
    meta = {
        "git": ["192.0.2.0/24"],
        "web": ["198.51.100.0/24"],
        "domains": {"website": ["github.com"], "codespaces": ["x.example"]},
    }
    monkeypatch.setattr(er, "fetch_json", lambda url: meta)
    domains, ips = er.github_meta(
        {"ip_keys": ["git", "absent"], "domain_categories": ["website", "none"]}
    )
    assert domains == ["github.com"]
    assert ips == ["192.0.2.0/24"]
    assert er.github_meta({}) == ([], [])


def test_aws_ip_ranges_filters_by_service_and_region(er, monkeypatch):
    data = {
        "prefixes": [
            {"ip_prefix": "192.0.2.0/24", "service": "AMAZON", "region": "us-east-1"},
            {"ip_prefix": "198.51.100.0/24", "service": "EC2", "region": "us-east-1"},
            {"ip_prefix": "203.0.113.0/24", "service": "AMAZON", "region": "eu-west-1"},
        ],
        "ipv6_prefixes": [
            {"ipv6_prefix": "2001:db8::/32", "service": "AMAZON", "region": "us-east-1"}
        ],
    }
    monkeypatch.setattr(er, "fetch_json", lambda url: data)
    assert er.aws_ip_ranges({}) == (
        [],
        ["192.0.2.0/24", "203.0.113.0/24", "2001:db8::/32"],
    )
    assert er.aws_ip_ranges({"services": ["AMAZON"], "regions": ["us-east-1"]}) == (
        [],
        ["192.0.2.0/24", "2001:db8::/32"],
    )


def test_collapse_merges_each_family():
    er = load(SCRIPT, "egress_refresh")
    assert er.collapse(
        [
            "10.0.0.0/25",
            "10.0.0.128/25",
            "10.0.0.5/32",
            "2001:db8::1/128",
            "2001:db8::/64",
        ]
    ) == ["10.0.0.0/24", "2001:db8::/64"]


def provider(name="github-meta", **extra):
    return {"name": name, **extra}


def test_provider_data_uses_the_cache_when_asked(er):
    er.CACHE.mkdir()
    (er.CACHE / "s.json").write_text(
        json.dumps({"domains": ["a"], "ips": ["192.0.2.0/24"], "fetched": "t"})
    )
    assert er.provider_data("s", provider(), cached=True) == (["a"], ["192.0.2.0/24"])


def test_provider_data_fetches_and_caches(er, monkeypatch, capsys):
    monkeypatch.setitem(
        er.PROVIDERS,
        "github-meta",
        lambda opts: (["d"], ["192.0.2.0/25", "192.0.2.128/25"]),
    )
    # cached, but nothing cached yet: asks the provider.
    assert er.provider_data("s", provider(enforce_ips=True), cached=True) == (
        ["d"],
        ["192.0.2.0/24"],
    )
    record = json.loads((er.CACHE / "s.json").read_text())
    assert record["ips"] == ["192.0.2.0/24"]
    assert "gave 1 domains, 2 ranges (1 after merging)" in capsys.readouterr().err


def test_provider_data_without_ranges_when_enforcing_falls_back_to_the_cache(
    er, monkeypatch, capsys
):
    monkeypatch.setitem(er.PROVIDERS, "github-meta", lambda opts: (["d"], []))
    er.CACHE.mkdir()
    (er.CACHE / "s.json").write_text(
        json.dumps({"domains": ["old"], "ips": ["x"], "fetched": "then"})
    )
    assert er.provider_data("s", provider(enforce_ips=True)) == (["old"], ["x"])
    assert "using the copy from then" in capsys.readouterr().err


def test_provider_data_failure_with_nothing_cached_keeps_static_domains(
    er, monkeypatch, capsys
):
    def broken(opts):
        raise OSError("down")

    monkeypatch.setitem(er.PROVIDERS, "github-meta", broken)
    assert er.provider_data("s", provider()) == ([], [])
    assert "nothing is cached" in capsys.readouterr().err


def test_provider_data_unenforced_without_ranges_is_still_fresh(er, monkeypatch):
    monkeypatch.setitem(er.PROVIDERS, "github-meta", lambda opts: (["d"], []))
    assert er.provider_data("s", provider()) == (["d"], [])


def test_provider_data_survives_a_cache_it_cannot_write(er, monkeypatch, capsys):
    monkeypatch.setitem(
        er.PROVIDERS, "github-meta", lambda opts: ([], ["192.0.2.0/24"])
    )
    er.CACHE.write_text("a file where the directory should be")
    assert er.provider_data("s", provider()) == ([], ["192.0.2.0/24"])
    assert "could not be cached" in capsys.readouterr().err


def test_squid_domains_drops_names_a_wildcard_covers():
    er = load(SCRIPT, "egress_refresh")
    assert er.squid_domains(
        ["*.Example.com.", "example.com", "a.example.com", " other.org ", "*.other.org"]
    ) == [".example.com", ".other.org"]
    assert er.squid_domains(["b.test", "a.test"]) == ["a.test", "b.test"]


def test_load_reads_a_set(er):
    write_set(er, "x", 'description = "X"\ndomains = ["x.test"]\n')
    assert er.load("x") == {"description": "X", "domains": ["x.test"]}


def test_load_refuses_an_unknown_set(er, capsys):
    write_set(er, "known", "")
    with pytest.raises(SystemExit) as e:
        er.load("absent")
    assert e.value.code == 2
    assert "known sets: known" in capsys.readouterr().err


def test_load_refuses_a_broken_set(er, capsys):
    write_set(er, "bad", "this is = = not toml")
    with pytest.raises(SystemExit) as e:
        er.load("bad")
    assert e.value.code == 2
    assert "bad.toml" in capsys.readouterr().err


def test_workspaces_without_a_directory_is_empty(er):
    assert er.workspaces() == []


def test_workspaces_reads_each_registration(er):
    register(er, "b", "subnet=10.203.8.0/24\n")
    register(
        er, "a", "# comment\nother=1\nsets = python, ,github\nsubnet = 10.203.7.0/24\n"
    )
    found = er.workspaces()
    assert [(n, str(s), sets) for n, s, sets in found] == [
        ("a", "10.203.7.0/24", ["workbench", "github", "ghcr", "python"]),
        ("b", "10.203.8.0/24", ["workbench", "github", "ghcr"]),
    ]


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("a", "sets=python\n", "no valid subnet"),
        ("a", "subnet=192.168.0.0/24\n", "a workspace is a name and a subnet"),
        ("bad name", "subnet=10.203.7.0/24\n", "a workspace is a name and a subnet"),
    ],
)
def test_workspaces_refuses_a_bad_registration(er, capsys, name, text, message):
    register(er, name, text)
    with pytest.raises(SystemExit) as e:
        er.workspaces()
    assert e.value.code == 2
    assert message in capsys.readouterr().err


def test_set_rules_domains_only(er):
    er.OUT.mkdir()
    write_set(er, "plain-set", 'domains = ["*.a.test", "b.test"]\n')
    lines, match = er.set_rules("plain-set", False)
    assert match == "set_plain_set"
    assert lines == [f'acl set_plain_set dstdomain -n "{er.OUT / "plain-set.domains"}"']
    assert (er.OUT / "plain-set.domains").read_text() == ".a.test\nb.test\n"


def test_set_rules_with_enforced_ranges(er, monkeypatch):
    er.OUT.mkdir()
    write_set(
        er,
        "gh",
        'domains = ["a.test"]\n[provider]\nname = "github-meta"\nenforce_ips = true\n',
    )
    monkeypatch.setattr(
        er, "provider_data", lambda name, prov, cached: (["c.test"], ["192.0.2.0/24"])
    )
    lines, match = er.set_rules("gh", True)
    assert match == "set_gh set_gh_ips"
    assert lines[1] == f'acl set_gh_ips dst "{er.OUT / "gh.ips"}"'
    assert (er.OUT / "gh.ips").read_text() == "192.0.2.0/24\n"
    assert (er.OUT / "gh.domains").read_text() == "a.test\nc.test\n"


def test_set_rules_enforcing_set_without_ranges_keeps_the_domains(er, monkeypatch):
    er.OUT.mkdir()
    write_set(er, "gh", '[provider]\nname = "github-meta"\nenforce_ips = true\n')
    monkeypatch.setattr(er, "provider_data", lambda name, prov, cached: ([], []))
    assert er.set_rules("gh", False)[1] == "set_gh"


def test_set_rules_refuses_an_unknown_provider(er, capsys):
    write_set(er, "odd", '[provider]\nname = "nobody"\n')
    with pytest.raises(SystemExit) as e:
        er.set_rules("odd", False)
    assert e.value.code == 2
    assert "unknown provider 'nobody'" in capsys.readouterr().err


def test_main_lists_the_real_sets(er, monkeypatch, capsys):
    monkeypatch.setattr(er, "SETS_DIR", REPO_ROOT / "images/egress-proxy/sets")
    assert er.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("alpine")
    assert "workbench" in out


def test_main_writes_rules_for_each_workspace(er, monkeypatch, capsys):
    for name in ("workbench", "github", "ghcr", "python"):
        write_set(er, name, f'domains = ["{name}.test"]\n')
    register(er, "one", "subnet=10.203.7.0/24\nsets=python\n")
    register(er, "two", "subnet=10.203.8.0/24\n")
    assert er.main(["--cached"]) == 0
    conf = (er.OUT / "sets.conf").read_text()
    assert conf.startswith("# Generated by egress-refresh")
    assert "acl ws0 src 10.203.7.0/24" in conf
    assert "http_access allow ws0 ws0_in set_python" in conf
    assert "http_access allow ws1 ws1_in set_ghcr" in conf
    assert "http_access allow ws1 ws1_in set_python" not in conf
    assert conf.count("acl set_python ") == 1
    assert "one (10.203.7.0/24)" in capsys.readouterr().err


def test_main_with_no_workspaces_refuses_everything(er, capsys):
    assert er.main([]) == 0
    assert (
        er.OUT / "sets.conf"
    ).read_text() == "# Generated by egress-refresh; do not edit.\n\n"
    assert "no workspaces registered" in capsys.readouterr().err
