#!/usr/bin/env python3
"""Checks the listings of the Schemify package index.

    python3 check/check.py --base origin/main [--offline] [packages/<name>...]

Without file arguments it checks every packages/ file the branch changed against --base.
For each file:

  errors (exit 1)
    - the format: the entry block of the Schemify store spec, read exactly as the app reads
      it (parse_entry below; test_check.py pins it to the app's reader with shared fixtures)
    - the file name is the entry name; https:// URLs only; no `verified` or `installs`
      line (Scouren sets those)
    - every URL fetched: release modules, icons, banners, screenshots and README images
      must hash to their sha256; images must be PNG (JPEG is refused) within their caps
      (icon 64 KiB and 256 x 256, banner 512 KiB, screenshot and image 1 MiB); README and
      changelog UTF-8 of at most 64 KiB
    - releases listed before keep their lines; the publisher does not change
  review flags (printed, and in the job summary; a reviewer decides)
    - grants a release adds over the one before it (grant growth)
    - risky grants (spawn, network, writes, read:*), a paid entry, a new publisher
    - README style: raw HTML, images not listed or not alone on their line

The format and the caps follow the store spec of the Schemify app; see CONTRIBUTING.md.
"""

import calendar
import hashlib
import os
import re
import subprocess
import sys
import urllib.request

LINE_MAX = 512
TEXT_MAX = 64 << 10
ICON_MAX, BANNER_MAX, SHOT_MAX = 64 << 10, 512 << 10, 1 << 20
ICON_PX = 256
CATEGORIES = ("sizing simulation layout interop verification docs agent pdk dark light "
              "high-contrast vscode").split()
KINDS = ("plugin", "theme", "tool", "pdk", "skill")
INCLUDES = ("canvas", "chrome", "waves", "docs")
VARIANTS = ("dark", "light", "high-contrast")
RISKY = re.compile(r"^(spawn|net|write|read:\*|commands)")
PNG = b"\x89PNG\r\n\x1a\n"


class Bad(Exception):
    """A malformed line: `line` is 1-based, `what` names the field expected there."""

    def __init__(self, line, what):
        super().__init__(f"line {line}: expected {what}")
        self.line, self.what = line, what


class Lines:
    """The app's line reader: `name value` lines, LF only, printable ASCII."""

    def __init__(self, text):
        self.text, self.at, self.line = text, 0, 0

    def field(self, name):
        self.line += 1
        end = self.text.find("\n", self.at)
        if end < 0:
            raise Bad(self.line, name)
        line = self.text[self.at:end]
        if line == name:
            value = ""
        elif line.startswith(name + " "):
            value = line[len(name) + 1:]
        else:
            raise Bad(self.line, name)
        if any(not " " <= c <= "~" for c in value):
            raise Bad(self.line, name)
        self.at = end + 1
        return value

    def peek(self, name):
        rest = self.text[self.at:]
        return rest.startswith(name + " ") or rest.startswith(name + "\n")


def word(s, n, ok):
    return 1 <= len(s) <= n and all(ok(c) for c in s)


def url(s):
    for p in ("https://", "file://"):
        if s.startswith(p) and len(s) > len(p):
            return len(s) <= LINE_MAX and " " not in s
    return False


def sha(h):
    return len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def name_word(w):
    return word(w, 64, lambda c: c.islower() or c.isdigit() or c in "._-") and w[0] != "."


def version(v):
    parts = v.split(".")
    if len(parts) != 3:
        return None
    for p in parts:
        if not p.isdigit() or not p.isascii() or (p != "0" and p.startswith("0")) or int(p) >= 2**32:
            return None
    return tuple(int(p) for p in parts)


def day(d):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", d)
    if not m:
        return False
    y, mo, da = map(int, m.groups())
    return 1970 <= y <= 9999 and 1 <= mo <= 12 and 1 <= da <= calendar.monthrange(y, mo)[1]


def media(v):
    u, _, h = v.partition(" ")
    return {"url": u, "sha256": h} if url(u) and sha(h) else None


def parse_entry(lines):
    """One entry block at the cursor, as the app's reader parses it."""
    l = lines
    name = l.field("entry")
    if not name_word(name):
        raise Bad(l.line, "entry <name>")
    e = {"name": name, "line": l.line}
    e["publisher"] = l.field("publisher")
    if not word(e["publisher"], 39, lambda c: c.islower() or c.isdigit() or c == "-"):
        raise Bad(l.line, "publisher")
    e["kind"] = l.field("kind")
    if e["kind"] not in KINDS:
        raise Bad(l.line, "kind")
    e["license"] = l.field("license")
    if not word(e["license"], 64, lambda c: c.isascii() and (c.isalnum() or c in ".+-")):
        raise Bad(l.line, "license")
    cats = l.field("categories").split(" ")
    if not 1 <= len(cats) <= 4 or any(c not in CATEGORIES for c in cats):
        raise Bad(l.line, "categories")
    e["categories"] = cats
    e["summary"] = l.field("summary")
    if not 1 <= len(e["summary"]) <= LINE_MAX:
        raise Bad(l.line, "summary")
    e["readme"] = l.field("readme")
    if e["readme"] and not url(e["readme"]):
        raise Bad(l.line, "readme")
    e["paid"] = None
    if l.peek("paid"):
        w = l.field("paid").split(" ")
        ok = len(w) == 3 and w[0].isdigit() and w[0].isascii() and not w[0].startswith("0")
        ok = ok and 1 <= int(w[0]) <= 100_000_000 and name_word(w[1])
        ok = ok and word(w[2], 64, lambda c: c.isascii() and (c.isalnum() or c in "_-"))
        if not ok:
            raise Bad(l.line, "paid")
        e["paid"] = w
    shown(l, e)
    e["releases"] = []
    while l.peek("release"):
        w = l.field("release").split(" ")
        ok = len(w) == 4 and version(w[0]) and url(w[2])
        commit = len(w) == 4 and len(w[3]) == 40 and all(c in "0123456789abcdef" for c in w[3])
        ok = ok and ((w[1] == "url" and sha(w[3])) or (w[1] == "git" and commit))
        rels = e["releases"]
        if not ok:
            raise Bad(l.line, "release")
        if len(rels) == 8 or (rels and rels[-1]["version"] <= version(w[0])):
            raise Bad(l.line, "releases newest first")
        r = {"version": version(w[0]), "how": w[1], "url": w[2], "pin": w[3], "grants": [], "tools": [], "line": l.line}
        if l.peek("asset"):
            a = l.field("asset").partition(" ")
            if not (a[0].isdigit() and a[0].isascii() and not a[0].startswith("0") and int(a[0]) < 2**32 and day(a[2])):
                raise Bad(l.line, "asset")
        while l.peek("grant"):
            g, _, reason = l.field("grant").partition(" ")
            fresh = all(x[0] != g for x in r["grants"])
            if not (_ and word(g, 128, lambda c: "!" <= c <= "~") and len(reason) <= LINE_MAX and fresh and len(r["grants"]) < 16):
                raise Bad(l.line, "grant")
            r["grants"].append((g, reason))
        while l.peek("tool"):
            t, _, d = l.field("tool").partition(" ")
            fresh = all(x != t for x in r["tools"])
            if not (_ and name_word(t) and 1 <= len(d) <= LINE_MAX and fresh and len(r["tools"]) < 32):
                raise Bad(l.line, "tool")
            r["tools"].append(t)
        rels.append(r)
    if not e["releases"]:
        raise Bad(l.line + 1, "release")
    return e


def shown(l, e):
    """The optional display lines, in the spec's order."""
    e["verified"] = l.peek("verified")
    if e["verified"] and l.field("verified"):
        raise Bad(l.line, "verified")
    e["installs"] = None
    if l.peek("installs"):
        v = l.field("installs")
        if not (v.isdigit() and v.isascii() and (v == "0" or not v.startswith("0")) and int(v) <= 10**9):
            raise Bad(l.line, "installs")
        e["installs"] = int(v)
    e["media"] = []
    for kind in ("icon", "banner"):
        if l.peek(kind):
            m = media(l.field(kind))
            if not m:
                raise Bad(l.line, kind)
            e["media"].append((kind, m))
    n = 0
    while l.peek("screenshot"):
        v = l.field("screenshot").split(" ", 2)
        m = media(" ".join(v[:2])) if len(v) == 3 and len(v[2]) <= LINE_MAX else None
        if n == 6:
            raise Bad(l.line, "at most 6 screenshots")
        if not m:
            raise Bad(l.line, "screenshot")
        e["media"].append(("screenshot", m))
        n += 1
    e["changelog"] = None
    if l.peek("changelog"):
        e["changelog"] = l.field("changelog")
        if not url(e["changelog"]):
            raise Bad(l.line, "changelog")
    n = 0
    while l.peek("image"):
        v = l.field("image")
        if n == 16:
            raise Bad(l.line, "at most 16 images")
        m = media(v)
        if not m:
            raise Bad(l.line, "image")
        e["media"].append(("image", m))
        n += 1
    for kind, allowed in (("includes", INCLUDES), ("variants", VARIANTS)):
        if l.peek(kind):
            w = l.field(kind).split(" ")
            if not (1 <= len(w) <= 16 and len(set(w)) == len(w) and all(x in allowed for x in w)):
                raise Bad(l.line, kind)
    names = []
    while l.peek("token"):
        n_, _, h = l.field("token").partition(" ")
        ok = len(h) == 6 and all(c in "0123456789abcdef" for c in h) and name_word(n_) and n_ not in names
        if len(names) == 16:
            raise Bad(l.line, "at most 16 tokens")
        if not ok:
            raise Bad(l.line, "token")
        names.append(n_)


def entry_file(text):
    """A packages/<name> file: one entry block, nothing after it (the app's `entry_file`)."""
    if len(text) > 16 << 10 or not text.startswith("entry "):
        raise Bad(1, "entry <name>, at most 16 KiB")
    whole = text + "key 0\nsignature " + "0" * 128 + "\n"
    l = Lines(whole)
    entries = []
    while l.peek("entry"):
        e = parse_entry(l)
        if entries and entries[-1]["name"] >= e["name"]:
            raise Bad(l.line, "entries in ascending name order")
        entries.append(e)
        if len(entries) == 1:
            block_lines = l.line
    revoked = []
    while l.peek("revoked"):
        h = l.field("revoked")
        if not sha(h):
            raise Bad(l.line, "revoked")
        if len(revoked) == 4096 or (revoked and revoked[-1] >= h):
            raise Bad(l.line, "revoked hashes ascending")
        revoked.append(h)
    if l.field("key") != "0":
        raise Bad(l.line, "key")
    l.field("signature")
    if l.at != len(whole):
        raise Bad(l.line + 1, "end of file")
    if len(entries) > 1 or revoked:
        raise Bad(block_lines + 1, "one entry block, nothing after it")
    if entries[0]["installs"] == 0:
        raise Bad(1, "one entry block, nothing after it")  # the app writes no `installs 0`
    return entries[0]


def fetch(u, cap):
    with urllib.request.urlopen(u, timeout=60) as r:
        data = r.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"{u}: over {cap >> 10} KiB")
    return data


def resolve(base, link):
    """A README link as markdown resolves it against the README's URL (the app's rule)."""
    if "://" in link:
        return link
    rel = link[2:] if link.startswith("./") else link
    if not rel or rel.startswith("/") or ".." in rel.split("/") or "/" not in base:
        return None
    return base[: base.rfind("/") + 1] + rel


def check_online(e, errors, flags):
    caps = {"icon": ICON_MAX, "banner": BANNER_MAX, "screenshot": SHOT_MAX, "image": SHOT_MAX}
    for kind, m in e["media"]:
        try:
            data = fetch(m["url"], caps[kind])
        except Exception as x:
            errors.append(f"{kind} {m['url']}: {x}")
            continue
        if hashlib.sha256(data).hexdigest() != m["sha256"]:
            errors.append(f"{kind} {m['url']}: sha256 is not the listing's")
        elif data.startswith(b"\xff\xd8\xff"):
            errors.append(f"{kind} {m['url']}: JPEG; the store shows PNG only")
        elif not data.startswith(PNG):
            errors.append(f"{kind} {m['url']}: not a PNG")
        elif kind == "icon" and (data[12:16] != b"IHDR" or data[16:24] != ICON_PX.to_bytes(4, "big") * 2):
            errors.append(f"icon {m['url']}: not 256 x 256")
    for r in e["releases"]:
        if r["how"] != "url":
            continue
        try:
            data = fetch(r["url"], 64 << 20)
            if hashlib.sha256(data).hexdigest() != r["pin"]:
                errors.append(f"release {r['url']}: sha256 is not the listing's")
            elif not data.startswith(b"\0asm"):
                errors.append(f"release {r['url']}: not a wasm module")
        except Exception as x:
            errors.append(f"release {r['url']}: {x}")
    images = [m["url"] for k, m in e["media"] if k == "image"]
    for which in ("readme", "changelog"):
        u = e[which]
        if not u:
            continue
        try:
            text = fetch(u, TEXT_MAX).decode("utf-8")
        except Exception as x:
            errors.append(f"{which} {u}: {x}")
            continue
        if which == "readme":
            readme_style(u, text, images, flags)


def readme_style(base, text, images, flags):
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        t = line.strip()
        if t.startswith("```"):
            fence = not fence
        if fence:
            continue
        plain = "".join(t.split("`")[::2])
        if re.search(r"<[A-Za-z/!]", plain):
            flags.append(f"README:{n}: raw HTML (not shown in the store)")
        alone = t.startswith("![") and t.endswith(")")
        if "![" in plain and not alone:
            flags.append(f"README:{n}: an image not alone on its line (not shown)")
        if alone and resolve(base, t.rsplit("](", 1)[-1][:-1]) not in images:
            flags.append(f"README:{n}: an image not listed as `image` (not shown)")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def check_file(path, base, offline, publishers):
    errors, flags = [], []
    try:
        text = open(path, encoding="utf-8").read()
        e = entry_file(text)
    except (Bad, UnicodeDecodeError) as x:
        return [f"{path}: {x}"], []
    name = os.path.basename(path)
    if e["name"] != name:
        errors.append(f"lists entry {e['name']}; the file must be packages/{e['name']}")
    if e["verified"] or e["installs"] is not None:
        errors.append("`verified` and `installs` are set by Scouren, not in a pull request")
    urls = [e["readme"], e["changelog"]] + [m["url"] for _, m in e["media"]] + [r["url"] for r in e["releases"]]
    for u in filter(None, urls):
        if not u.startswith("https://"):
            errors.append(f"{u}: listings use https:// URLs")
    prev = git("show", f"{base}:packages/{name}") if base else None
    old = None
    if prev is not None and prev.returncode == 0:
        try:
            old = entry_file(prev.stdout)
        except Bad:
            old = None
    if old:
        if old["publisher"] != e["publisher"]:
            errors.append(f"publisher {e['publisher']} replaces {old['publisher']}")
        old_lines = prev.stdout.splitlines(keepends=True)
        for r in old["releases"]:
            v = ".".join(map(str, r["version"]))
            keep = next((x for x in e["releases"] if x["version"] == r["version"]), None)
            if keep and release_block(old_lines, r) != release_block(text.splitlines(keepends=True), keep):
                errors.append(f"release {v} was listed before; its lines may not change")
        before = set(g for g, _ in old["releases"][0]["grants"])
        for g, reason in e["releases"][0]["grants"]:
            if g not in before:
                flags.append(f"grant growth: {g} ({reason})")
    elif base:
        flags.append("a new listing")
    for i, r in enumerate(e["releases"][:-1]):
        older = set(g for g, _ in e["releases"][i + 1]["grants"])
        if not old:
            for g, _ in r["grants"]:
                if g not in older:
                    flags.append(f"grant growth in {'.'.join(map(str, r['version']))}: {g}")
    for g, _ in e["releases"][0]["grants"]:
        if RISKY.match(g):
            flags.append(f"risky grant: {g}")
    if e["paid"]:
        flags.append("a paid listing")
    if publishers is not None and e["publisher"] not in publishers:
        flags.append(f"a new publisher: {e['publisher']}")
    if not offline:
        check_online(e, errors, flags)
    return [f"{path}: {x}" for x in errors], [f"{path}: {x}" for x in sorted(set(flags))]


def release_block(lines, r):
    """The release's own lines (from its `release` line to the next release)."""
    start = r["line"] - 1
    end = start + 1
    while end < len(lines) and not lines[end].startswith("release "):
        end += 1
    return lines[start:end]


def main(argv):
    base, offline, files = None, False, []
    it = iter(argv)
    for a in it:
        if a == "--base":
            base = next(it)
        elif a == "--offline":
            offline = True
        else:
            files.append(a)
    if not files and base:
        out = git("diff", "--name-only", "--diff-filter=d", f"{base}...HEAD", "--", "packages/")
        files = [f for f in out.stdout.split() if f.startswith("packages/") and not os.path.basename(f).startswith(".")]
        gone = git("diff", "--name-only", "--diff-filter=D", f"{base}...HEAD", "--", "packages/")
        removed = gone.stdout.split()
    else:
        removed = []
    publishers = None
    if base:
        publishers = set()
        listed = git("ls-tree", "--name-only", f"{base}", "packages/").stdout.split()
        for f in listed:
            shown_ = git("show", f"{base}:{f}")
            m = re.search(r"^publisher (\S+)$", shown_.stdout, re.M)
            if m:
                publishers.add(m.group(1))
    errors, flags = [], []
    for f in files:
        e, fl = check_file(f, base, offline, publishers)
        errors += e
        flags += fl
    flags += [f"{f}: removes a listing" for f in removed]
    for x in errors:
        print(f"::error::{x}")
    for x in flags:
        print(f"review: {x}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as s:
            s.write(f"## Listing check\n\n{len(files)} file(s), {len(errors)} error(s)\n\n")
            if flags:
                s.write("### Needs a reviewer's eye\n\n" + "".join(f"- {x}\n" for x in flags))
    print(f"{len(files)} file(s), {len(errors)} error(s), {len(flags)} review flag(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
