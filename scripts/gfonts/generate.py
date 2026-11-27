#!/usr/bin/env python3
"""Generate the Mosio `@gfonts` registry (all of Google Fonts) from a pinned commit of github.com/google/fonts.

  python3 -I scripts/gfonts/generate.py --sha <40-hex commit> --workdir /fresh/empty/dir [--out registry/gfonts]
  python3 -I scripts/gfonts/generate.py --sha <40-hex commit> --src /path/to/checkout [--out registry/gfonts]

Standard library only. Run it with -I (isolated mode) so nothing next to the script or in the current directory is
imported. `--workdir` must not exist or be empty: the commit is fetched there (shallow, blob-filtered, sparse over
ofl/ apache/ ufl/) and treated as untrusted data. `--src` reuses a checkout that is already at that commit.

Per family directory under ofl/, apache/ and ufl/ it reads METADATA.pb (protobuf text format; a small parser below),
DESCRIPTION.en_us.html and the licence file, and writes
  <out>/font-<kebab-family>.json   the item (mosio:font; the faces are files referenced by raw.githubusercontent.com url
                                   at the pinned commit, with sha256 and size; the licence text is inline)
  <out>/registry.json              the index (metadata only)
  <out>/manifest.json              the pinned commit, the counts, and every skipped family with its reason
Refreshing is re-running with a newer commit: an item whose content did not change is left exactly as it is (same version,
commit and urls), a changed one gets the next patch version (1.0.0 -> 1.0.1) and the previous document is kept as
<name>@<version>.json. The manifest names the commit of the run; an item names the commit its files were last read at.

The item hash is documented in docs/REGISTRY.md of usemosio/mosio: SHA-256 over the files sorted by path, each as
`path NUL content NUL` (inline) or `path NUL url NUL sha256 NUL` (by url).
"""
import argparse
import hashlib
import html
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

MAX_FILE = 16 << 20  # engine limit per url file
MAX_FACES = 32  # engine limit of faces per font item
LICENSES = {"ofl": ("OFL-1.1", "OFL.txt"), "apache": ("Apache-2.0", "LICENSE.txt"), "ufl": ("UFL-1.0", "UFL.txt")}
CATEGORY = {"SANS_SERIF": "sans", "SERIF": "serif", "DISPLAY": "display", "HANDWRITING": "handwritten", "MONOSPACE": "mono"}
RAW = "https://raw.githubusercontent.com/google/fonts/"
URL_OK = re.compile(r"^https://raw\.githubusercontent\.com/google/fonts/[0-9a-f]{40}/[A-Za-z0-9._%/+-]+$")
SUBSET_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
ITEM_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
BAD_CSS = set('"\\{};<>')


# ---- protobuf text format ----------------------------------------------------------------------------------------

SKIP = re.compile(r"(?:\s|#[^\n]*)*")
TOKEN = re.compile(r'(?:(?P<str>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')|(?P<word>[A-Za-z0-9_.+\-]+)|(?P<p>[{}:\[\],;<>]))')


def _unescape(s):
    body = s[1:-1]
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        e = body[i]
        if e in "nrt":
            out.append({"n": "\n", "r": "\r", "t": "\t"}[e])
            i += 1
        elif e in "01234567":
            j = i
            while j < len(body) and j < i + 3 and body[j] in "01234567":
                j += 1
            out.append(bytes([int(body[i:j], 8)]).decode("latin-1"))
            i = j
        elif e == "x":
            j = i + 1
            while j < len(body) and j < i + 3 and body[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(bytes([int(body[i + 1:j], 16)]).decode("latin-1"))
            i = j
        else:
            out.append(e)
            i += 1
    # escapes above produced UTF-8 bytes one byte per char: re-decode when it is valid UTF-8
    s2 = "".join(out)
    try:
        return s2.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s2


def parse_pb(text):
    """Protobuf text format into dicts; a repeated key becomes a list in every case (`get` always returns lists)."""
    toks, pos = [], 0
    text = text.rstrip() + "\n"
    while True:
        pos = SKIP.match(text, pos).end()  # whitespace and # comments, up to the next token or the end
        if pos >= len(text):
            break
        m = TOKEN.match(text, pos)
        if not m:
            raise ValueError("cannot parse near %r" % text[pos:pos + 40])
        pos = m.end()
        if m.group("str") is not None:
            toks.append(("str", _unescape(m.group("str"))))
        elif m.group("word") is not None:
            toks.append(("word", m.group("word")))
        else:
            toks.append(("p", m.group("p")))
    # adjacent string literals concatenate
    merged = []
    for t in toks:
        if t[0] == "str" and merged and merged[-1][0] == "str":
            merged[-1] = ("str", merged[-1][1] + t[1])
        else:
            merged.append(t)
    i = 0

    def message(end_on_brace):
        nonlocal i
        d = {}
        while i < len(merged):
            kind, val = merged[i]
            if kind == "p" and val == "}" and end_on_brace:
                i += 1
                return d
            if kind == "p" and val in ";,":
                i += 1
                continue
            if kind != "word":
                raise ValueError("expected a field name, got %r" % (val,))
            key = val
            i += 1
            if i < len(merged) and merged[i] == ("p", ":"):
                i += 1
            kind, val = merged[i]
            if kind == "p" and val in "{<":
                i += 1
                v = message(True)
            elif kind == "p" and val == "[":
                i += 1
                v = []
                while merged[i] != ("p", "]"):
                    if merged[i] != ("p", ","):
                        v.append(merged[i][1])
                    i += 1
                i += 1
                d.setdefault(key, []).extend(v)
                continue
            else:
                v = val
                i += 1
            d.setdefault(key, []).append(v)
        if end_on_brace:
            raise ValueError("missing }")
        return d

    return message(False)


def first(d, key, default=None):
    v = d.get(key)
    return v[0] if v else default


# ---- text helpers ------------------------------------------------------------------------------------------------

class _Strip(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in ("p", "li", "div"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_line(src):
    p = _Strip()
    p.feed(src)
    p.close()
    t = html.unescape("".join(p.parts))
    t = "".join(c if c.isprintable() else " " for c in t)
    return re.sub(r"\s+", " ", t).strip()


def clip(t, n):
    if len(t) <= n:
        return t
    cut = t[:n]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if stop >= n // 2:
        return cut[:stop + 1]
    return cut[:cut.rfind(" ")].rstrip(" ,;:-") + "..." if " " in cut else cut


def kebab(s):
    s = s.lower().replace("&", " and ")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")


def read_text(path):
    b = open(path, "rb").read()
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        return b.decode("latin-1")


RFN = re.compile(r"with\s+Reserved\s+Font\s+Names?\s+(.+?)(?:\.\s|\.$|\n\n|\)\s*$)", re.I | re.S)


def reserved_names(license_text):
    """Reserved Font Names the licence's copyright line states: `... with Reserved Font Name "Abril Fatface".`"""
    names = []
    for m in RFN.finditer(license_text[:4000]):
        chunk = m.group(1)
        q = re.findall(r'["“‘\']([^"”’\']+)["”’\']', chunk)
        if not q:
            q = [re.split(r"[\n(]", chunk)[0].strip().rstrip(".,")]
        for n in q:
            n = re.sub(r"\s+", " ", n).strip()
            if n and n not in names and len(n) <= 60:
                names.append(n)
    return names


# ---- tags --------------------------------------------------------------------------------------------------------

HINTS = [
    # (tag, regex over name + description)
    ("retro", r"\bretro|vintage|nostalgi|old[- ]school|\b19[2-9]0s\b|mid-century"),
    ("art deco", r"art deco|deco\b"),
    ("elegant", r"elegan|refined|sophisticat|graceful|luxur"),
    ("playful", r"playful|fun\b|whims|quirk|cheerful|bubbly|cartoon"),
    ("friendly", r"friendly|warm|approachable|casual"),
    ("geometric", r"geometric"),
    ("humanist", r"humanist"),
    ("grotesque", r"grotesque|grotesk"),
    ("neo-grotesque", r"neo-?grotesque"),
    ("slab", r"slab"),
    ("didone", r"didone|high[- ]contrast|fat face"),
    ("blackletter", r"blackletter|fraktur|gothic lettering|old english"),
    ("script", r"\bscript\b|calligraph|cursive"),
    ("brush", r"\bbrush\b"),
    ("pixel", r"pixel|bitmap|8-bit"),
    ("stencil", r"stencil"),
    ("handwriting", r"handwrit|hand-written|hand drawn|hand-drawn|pen\b|marker"),
    ("comic", r"comic"),
    ("futuristic", r"futur|sci-?fi|techno|cyber|space\b"),
    ("typewriter", r"typewrit"),
    ("rounded", r"rounded|round\b"),
    ("condensed", r"condensed|narrow|compact"),
    ("headline", r"headline|poster|billboard|title|display"),
    ("editorial", r"editorial|newspaper|magazine|journal"),
    ("book", r"\bbook\b|long[- ]form|reading|body text|literature|novel"),
    ("ui", r"\bui\b|interface|screen|dashboard|legib"),
    ("code", r"\bcode\b|coding|terminal|programm"),
    ("decorative", r"decorat|ornament|ornate|fancy"),
    ("rustic", r"rustic|western|wood|grunge|rough"),
    ("modern", r"\bmodern|contemporary|clean|minimal"),
    ("traditional", r"traditional|classic|old-style|oldstyle|garalde"),
]


def tags_for(family, category, text, subsets, weights, variable, italic, classes):
    tags = [category]
    blob = (family + " " + text).lower()
    for tag, rx in HINTS:
        if tag not in tags and re.search(r"\b(?:" + rx + ")", blob):
            tags.append(tag)
    for c in classes:
        c = c.lower().replace("_", " ")
        if c not in tags:
            tags.append(c)
    if variable:
        tags.append("variable")
    if len(weights) >= 6:
        tags.append("many weights")
    if italic:
        tags.append("italic")
    for s in subsets:
        if s != "menu" and s not in tags:
            tags.append(s)
    out = []
    for t in tags:
        t = t[:40]
        if t and t not in out:
            out.append(t)
    return out[:32]


# ---- one family --------------------------------------------------------------------------------------------------

def snap_range(lo, hi, default):
    """A CSS weight for a face: `100`..`900` in steps of 100, or a range `lo hi` of those (the engine's rule)."""
    a = -(-int(round(lo)) // 100) * 100
    b = int(hi) // 100 * 100
    a, b = max(a, 100), min(b, 900)
    if a < b:
        return "%d %d" % (a, b)
    d = min(900, max(100, int(round(default / 100.0)) * 100))
    return str(d)


def local_name(filename):
    n = re.sub(r"\[([^\]]*)\]", lambda m: "-" + m.group(1).replace(",", "-"), filename)
    return re.sub(r"[^A-Za-z0-9._-]", "_", n)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def item_sha256(files):
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f["path"].encode()):
        h.update(f["path"].encode() + b"\0")
        if "url" in f:
            h.update(f["url"].encode() + b"\0" + f["sha256"].encode() + b"\0")
        else:
            h.update(f["content"].encode() + b"\0")
    return h.hexdigest()


class Skip(Exception):
    pass


def build_family(root, lic_dir, slug, sha):
    d = os.path.join(root, lic_dir, slug)
    license_id, license_file = LICENSES[lic_dir]
    meta_path = os.path.join(d, "METADATA.pb")
    if not os.path.isfile(meta_path):
        raise Skip("no METADATA.pb")
    try:
        meta = parse_pb(read_text(meta_path))
    except (ValueError, IndexError) as e:
        raise Skip("METADATA.pb does not parse (%s)" % e)
    family = first(meta, "name")
    if not family:
        raise Skip("METADATA.pb has no name")
    subsets = [s for s in meta.get("subsets", []) if SUBSET_OK.match(s)]
    if "latin" not in subsets:
        raise Skip("no latin subset (subsets: %s)" % (", ".join(s for s in meta.get("subsets", []) if s != "menu") or "none"))
    if len(family) > 100 or any(c in BAD_CSS or ord(c) < 32 for c in family):
        raise Skip("family name %r cannot go in CSS" % family)
    cats = meta.get("category", [])
    cat_key = next((c for c in cats if c in CATEGORY), None)
    if cat_key is None:
        raise Skip("unknown category %r" % (cats,))
    category = CATEGORY[cat_key]
    lic_meta = first(meta, "license", "")
    if {"OFL": "ofl", "APACHE2": "apache", "UFL": "ufl"}.get(lic_meta) not in (None, lic_dir):
        raise Skip("METADATA license %s does not match directory %s" % (lic_meta, lic_dir))
    lic_path = os.path.join(d, license_file)
    if not os.path.isfile(lic_path):
        alt = [f for f in ("OFL.txt", "LICENSE.txt", "UFL.txt", "LICENCE.txt") if os.path.isfile(os.path.join(d, f))]
        if not alt:
            raise Skip("no licence file (%s)" % license_file)
        license_file = alt[0]
        lic_path = os.path.join(d, license_file)
    lic_text = read_text(lic_path)
    if len(lic_text) > 40000:
        raise Skip("licence text is %d characters" % len(lic_text))
    axes = {a.get("tag", [""])[0]: (float(first(a, "min_value", 0)), float(first(a, "max_value", 0))) for a in meta.get("axes", [])}
    files, faces, used = [{"path": license_file, "role": "license", "content": lic_text}], [], set()
    weights, italic = set(), False
    fonts = meta.get("fonts", [])
    if not fonts:
        raise Skip("METADATA.pb lists no fonts")
    for fo in fonts:
        fn = first(fo, "filename")
        if not fn or not fn.lower().endswith((".ttf", ".otf")):
            raise Skip("font file %r is not .ttf or .otf" % fn)
        path = os.path.join(d, fn)
        if not os.path.isfile(path):
            raise Skip("font file %s is missing from the commit" % fn)
        size = os.path.getsize(path)
        if size > MAX_FILE:
            raise Skip("font file %s is %.1f MiB, over the 16 MiB limit" % (fn, size / 1048576))
        if size < 12:
            raise Skip("font file %s is empty" % fn)
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
            raise Skip("font file %s is not a TrueType/OpenType file" % fn)
        style = first(fo, "style", "normal")
        style = style if style in ("normal", "italic", "oblique") else "normal"
        w = int(float(first(fo, "weight", "400")))
        if "[" in fn and "wght" in axes:
            weight = snap_range(axes["wght"][0], axes["wght"][1], w)
        else:
            weight = str(min(900, max(100, int(round(w / 100.0)) * 100)))
        local = "fonts/" + local_name(fn)
        if local in used:
            raise Skip("two font files map to the name %s" % local)
        used.add(local)
        url = RAW + sha + "/" + lic_dir + "/" + slug + "/" + urllib.parse.quote(fn, safe="")
        if not URL_OK.match(url):
            raise Skip("url of %s has characters the engine does not allow" % fn)
        files.append({"path": local, "role": "font", "url": url, "sha256": sha256_file(path), "size": size})
        faces.append({"path": local, "weight": weight, "style": style})
        weights.update(int(x) for x in weight.split())
        italic = italic or style == "italic"
    if len(faces) > MAX_FACES:
        raise Skip("%d faces, over the engine limit of %d" % (len(faces), MAX_FACES))
    faces.sort(key=lambda f: (f["style"] != "normal", int(f["weight"].split()[0]), f["path"]))
    desc_path = os.path.join(d, "DESCRIPTION.en_us.html")
    desc = plain_line(read_text(desc_path)) if os.path.isfile(desc_path) else ""
    designer = first(meta, "designer", "")
    if not desc:
        desc = "%s, a %s typeface%s." % (family, category, " by " + designer if designer else "")
    variable = any("[" in first(fo, "filename", "") for fo in fonts) and bool(axes)
    classes = [c for c in meta.get("classifications", []) if isinstance(c, str)]
    tags = tags_for(family, category, desc, subsets, weights, variable, italic, classes)
    name = "font-" + kebab(family)
    if not ITEM_OK.match(name):
        raise Skip("item name %r is not a valid registry name" % name)
    item = {
        "name": name,
        "type": "mosio:font",
        "version": "1.0.0",
        "license": license_id,
        "author": designer,
        "source": {"url": "https://github.com/google/fonts/tree/%s/%s/%s" % (sha, lic_dir, slug), "commit": sha},
        "family": family,
        "category": category,
        "tags": tags,
        "subsets": [s for s in subsets if s != "menu"],
        "variable": variable,
        "description": clip(desc, 600),
        "faces": faces,
        "files": files,
    }
    rfn = reserved_names(lic_text)
    if rfn:
        item["reserved_font_name"] = rfn
    if not item["author"]:
        del item["author"]
    item["sha256"] = item_sha256(files)
    return item


# ---- source ------------------------------------------------------------------------------------------------------

def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch(sha, workdir):
    if os.path.exists(workdir) and os.listdir(workdir):
        sys.exit("--workdir %s exists and is not empty: use a fresh directory" % workdir)
    os.makedirs(workdir, exist_ok=True)
    git("init", "-q", ".", cwd=workdir)
    git("remote", "add", "origin", "https://github.com/google/fonts", cwd=workdir)
    git("-c", "advice.detachedHead=false", "fetch", "--depth", "1", "--filter=blob:none", "origin", sha, cwd=workdir)
    git("sparse-checkout", "init", "--no-cone", cwd=workdir)
    git("sparse-checkout", "set", "/ofl/**", "/apache/**", "/ufl/**", cwd=workdir)
    git("checkout", "-q", "FETCH_HEAD", cwd=workdir)
    return workdir


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sha", required=True, help="google/fonts commit (40 hex)")
    ap.add_argument("--src", help="a checkout of google/fonts at --sha")
    ap.add_argument("--workdir", help="a fresh directory to fetch --sha into")
    ap.add_argument("--out", default="registry/gfonts")
    ap.add_argument("--only", help="comma-separated family directory names (testing)")
    a = ap.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", a.sha):
        sys.exit("--sha must be 40 lowercase hex characters")
    if bool(a.src) == bool(a.workdir):
        sys.exit("give exactly one of --src and --workdir")
    root = a.src or fetch(a.sha, a.workdir)
    head = git("rev-parse", "HEAD", cwd=root)
    if head != a.sha:
        sys.exit("the checkout is at %s, not %s" % (head, a.sha))
    os.makedirs(a.out, exist_ok=True)

    items, skipped, names = [], [], {}
    only = set(a.only.split(",")) if a.only else None
    for lic_dir in ("ofl", "apache", "ufl"):
        base = os.path.join(root, lic_dir)
        for slug in sorted(os.listdir(base) if os.path.isdir(base) else []):
            if not os.path.isdir(os.path.join(base, slug)) or (only and slug not in only):
                continue
            try:
                it = build_family(root, lic_dir, slug, a.sha)
            except Skip as e:
                skipped.append({"dir": "%s/%s" % (lic_dir, slug), "reason": str(e)})
                continue
            if it["name"] in names:
                skipped.append({"dir": "%s/%s" % (lic_dir, slug), "family": it["family"], "reason": "item name %s is taken by %s" % (it["name"], names[it["name"]])})
                continue
            names[it["name"]] = "%s/%s" % (lic_dir, slug)
            items.append(it)

    # versions: an item whose content (metadata, licence text, the sha256 and size of every face) did not change is kept
    # as it is, with the commit and urls it already has (they stay valid: they name a commit of google/fonts), so a
    # refresh rewrites only the families that changed. A changed item gets the next patch version and the previous
    # document is kept as <name>@<version>.json.
    def content(it):
        c = {k: v for k, v in it.items() if k not in ("version", "sha256", "source")}
        c["files"] = [{k: v for k, v in f.items() if k != "url"} for f in it["files"]]
        return c

    bumped = new = same = 0
    for n, it in enumerate(items):
        path = os.path.join(a.out, it["name"] + ".json")
        if os.path.isfile(path):
            old = json.load(open(path))
            if content(old) == content(it):
                items[n] = old
                same += 1
                continue
            parts = [int(x) for x in old["version"].split(".")]
            parts[2] += 1
            it["version"] = "%d.%d.%d" % tuple(parts)
            shutil.copyfile(path, os.path.join(a.out, "%s@%s.json" % (it["name"], old["version"])))
            bumped += 1
        else:
            new += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(it, f, ensure_ascii=False, indent=1)
            f.write("\n")
    keep = {it["name"] + ".json" for it in items}
    for fn in os.listdir(a.out):
        if fn.endswith(".json") and fn.startswith("font-") and "@" not in fn and fn not in keep:
            os.remove(os.path.join(a.out, fn))  # a family that left the commit or is now skipped

    items.sort(key=lambda i: i["name"])
    index = {"$schema": "https://mosio.me/schema/registry.json", "name": "gfonts", "homepage": "https://fonts.google.com", "items": [
        {"name": i["name"], "type": i["type"], "title": i["family"], "description": clip(i["description"], 240), "tags": i["tags"],
         "version": i["version"], "license": i["license"], "family": i["family"], "category": i["category"], "subsets": i["subsets"],
         "variable": i["variable"]} for i in items]}
    with open(os.path.join(a.out, "registry.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")

    def count(key):
        c = {}
        for i in items:
            c[i[key]] = c.get(i[key], 0) + 1
        return dict(sorted(c.items()))

    reasons = {}
    for s in skipped:
        k = re.sub(r"^font file .* over the 16 MiB limit$", "a font file is over the 16 MiB limit", s["reason"])
        k = re.sub(r"\s*\(.*$", "", k)
        reasons[k] = reasons.get(k, 0) + 1
    manifest = {
        "source": "https://github.com/google/fonts", "commit": a.sha, "generator": "scripts/gfonts/generate.py",
        "counts": {"families": len(items), "skipped": len(skipped), "faces": sum(len(i["faces"]) for i in items),
                   "variable": sum(1 for i in items if i["variable"]), "by_license": count("license"), "by_category": count("category"),
                   "with_reserved_font_name": sum(1 for i in items if "reserved_font_name" in i),
                   "new": new, "changed": bumped, "unchanged": same},
        "skipped_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])),
        "skipped": sorted(skipped, key=lambda s: s["dir"]),
    }
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("families %d, skipped %d, faces %d (new %d, changed %d, unchanged %d)" % (len(items), len(skipped), manifest["counts"]["faces"], new, bumped, same))


if __name__ == "__main__":
    main()
