#!/usr/bin/env python3
"""Tests for check.py. The format oracle is the Schemify app's own reader: fixtures/ is a copy
of the Schemify app's fixture listings, and fixtures/EXPECT holds the
verdict the app gives each file (`ok`, or the line it refuses). Copy both again whenever the
app's format changes.

    python3 check/test_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import check  # noqa: E402

HERE = os.path.join(os.path.dirname(__file__), "fixtures")


def test_fixture_verdicts_match_the_app():
    expect = dict(l.split() for l in open(os.path.join(HERE, "EXPECT")))
    names = sorted(n for n in os.listdir(HERE) if n != "EXPECT")
    assert sorted(expect) == names, "EXPECT lists every fixture"
    for n in names:
        text = open(os.path.join(HERE, n)).read()
        try:
            check.entry_file(text)
            got = "ok"
        except check.Bad as b:
            got = str(b.line)
        assert got == expect[n], f"{n}: app says {expect[n]}, checker says {got}"


def test_readme_links_resolve_as_the_app_resolves_them():
    base = "https://e.org/v1/README.md"
    assert check.resolve(base, "docs/a.png") == "https://e.org/v1/docs/a.png"
    assert check.resolve(base, "./a.png") == "https://e.org/v1/a.png"
    assert check.resolve(base, "/a.png") is None
    assert check.resolve(base, "../a.png") is None


def test_file_rules_offline():
    good = open(os.path.join(HERE, "good-plugin")).read().replace("https://example.org", "https://e.org")
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "packages"))
        path = os.path.join(d, "packages", "gmid")
        open(path, "w").write(good)
        errors, flags = check.check_file(path, None, True, set())
        assert errors == [], errors
        assert any("risky grant: write:project" in f for f in flags), flags
        assert any("a new publisher: openfat" in f for f in flags), flags
        assert any("grant growth in 0.5.0: read:PDK_ROOT" in f for f in flags), flags
        wrong = os.path.join(d, "packages", "other")
        open(wrong, "w").write(good)
        errors, _ = check.check_file(wrong, None, True, None)
        assert any("must be packages/gmid" in e for e in errors), errors
        open(path, "w").write(good.replace("readme https", "readme http"))
        errors, _ = check.check_file(path, None, True, None)
        assert errors and "line 7" in errors[0], errors


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
