#!/usr/bin/env python3
"""parse_structure.py — Stage S1 (deterministic, no LLM).

Discover Markdown files under a ROOT, derive a unique slug per file, parse each
file's heading tree (with GitHub-style anchors de-duplicated within the file),
classify outbound links, parse optional YAML front matter, detect prose
language, and build a whole-corpus dependency graph (strong + weak edges).

Emits ``structure.json`` to stdout as valid UTF-8 JSON (see references/schemas.md
§1 for the exact shape). Errors go to stderr; a bad/unreadable ROOT exits
non-zero.

Usage:
    python3 parse_structure.py [ROOT] [--exclude DIR ...] [--output-language LANG]

Defaults:
    ROOT              = current directory
    --exclude         = .git .claude node_modules .mdHumanViewer dist build
                        .venv venv __pycache__
    --output-language = source

``--exclude`` overrides the defaults entirely (passing custom names means the
default-noise dirs are no longer skipped). Pure discovery + parse: this script
never writes anything and never calls an LLM.
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter

DEFAULT_EXCLUDES = [".git", ".claude", "node_modules", ".mdHumanViewer",
                    "dist", "build", ".venv", "venv", "__pycache__"]
MD_EXTS = (".md", ".markdown")


# --------------------------------------------------------------------------- #
# Slug & anchor rules (single-sourced here per references/schemas.md §slug).
# --------------------------------------------------------------------------- #

def slugify_path(rel_path):
    """Filesystem/anchor-safe slug from a relative path.

    Lowercase, drop the extension, collapse non-alphanumeric runs to '-'.
    Path separators are folded in so the slug stays unique across directories.
    """
    no_ext = re.sub(r"\.(md|markdown)$", "", rel_path, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", no_ext).strip("-").lower()
    return slug or "file"


def anchorize(text):
    """GitHub-style heading anchor from heading text (before de-dup).

    Lowercase, strip everything that is not a word char, space, or hyphen,
    then turn spaces into hyphens. (Underscores are kept, matching GitHub.)
    """
    a = text.strip().lower()
    a = re.sub(r"[^\w\s-]", "", a, flags=re.UNICODE)
    a = a.replace(" ", "-")
    return a


def humanize_filename(rel_path):
    """Filename without extension, in human-readable case.

    ``auth-skill.md`` -> ``Auth skill``.
    """
    base = os.path.basename(rel_path)
    base = re.sub(r"\.(md|markdown)$", "", base, flags=re.IGNORECASE)
    words = re.split(r"[^a-zA-Z0-9]+", base)
    words = [w for w in words if w]
    if not words:
        return base or rel_path
    first = words[0]
    first = first[:1].upper() + first[1:]
    rest = [w.lower() for w in words[1:]]
    return " ".join([first, *rest])


def estimate_tokens(text):
    """Cheap, honest token estimate: characters / 4 (never a real tokenizer)."""
    return max(1, round(len(text) / 4))


# --------------------------------------------------------------------------- #
# Front matter.
# --------------------------------------------------------------------------- #

def parse_frontmatter(text):
    """Parse a leading ``---`` YAML front-matter block into a flat dict.

    Stdlib-only: a deliberately small subset (``key: value`` lines plus simple
    ``- item`` lists). Returns None when there is no front matter (so the caller
    omits the field entirely rather than emitting an empty object).
    """
    if not text.startswith("---"):
        return None
    # The opening fence must be a line of its own.
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    result = {}
    current_key = None
    current_list = None
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # List item belonging to the most recent key.
        list_m = re.match(r"^\s*-\s+(.*)$", line)
        if list_m and current_key is not None:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(_scalar(list_m.group(1).strip()))
            continue
        kv = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", line)
        if kv:
            key = kv.group(1)
            val = kv.group(2).strip()
            current_key = key
            current_list = None
            if val == "":
                # Could be the head of a block list; default to empty string
                # and let any following "- item" lines convert it to a list.
                result[key] = ""
            else:
                result[key] = _scalar(val)
    return result if result else {}


def _scalar(val):
    """Coerce a YAML scalar string into bool/int/float/str (stdlib-only)."""
    if (len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"')):
        return val[1:-1]
    low = val.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", val):
        try:
            return int(val)
        except ValueError:
            return val
    if re.fullmatch(r"-?\d+\.\d+", val):
        try:
            return float(val)
        except ValueError:
            return val
    return val


def strip_frontmatter(text):
    """Return the body with a leading front-matter block removed (if present)."""
    if not text.startswith("---"):
        return text
    m = re.match(r"^---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", text, re.DOTALL)
    if not m:
        return text
    return text[m.end():]


# --------------------------------------------------------------------------- #
# Headings.
# --------------------------------------------------------------------------- #

def parse_headings(body):
    """Parse ATX headings into ``[{level, text, anchor}]`` with per-file de-dup.

    Fenced code blocks are skipped so ``# not a heading`` inside ``` fences is
    ignored. Anchors are de-duplicated GitHub-style within the file: first bare,
    duplicates suffixed ``-1``, ``-2``, ...
    """
    headings = []
    seen = {}
    in_fence = False
    fence_marker = None
    for line in body.splitlines():
        stripped = line.strip()
        fence_m = re.match(r"^(```+|~~~+)", stripped)
        if fence_m:
            marker = fence_m.group(1)[0] * 3
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = None
            continue
        if in_fence:
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if not h:
            continue
        level = len(h.group(1))
        raw_text = h.group(2).strip()
        if raw_text == "":
            continue
        text = _strip_inline_md(raw_text)
        base = anchorize(text)
        if base in seen:
            seen[base] += 1
            anchor = f"{base}-{seen[base]}"
        else:
            seen[base] = 0
            anchor = base
        headings.append({"level": level, "text": text, "anchor": anchor})
    return headings


def _strip_inline_md(text):
    """Strip a little inline markdown from heading text for a clean title.

    Removes link wrappers, emphasis, and inline-code backticks; the anchor is
    derived from the cleaned text.
    """
    # [label](url) -> label
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Inline code: drop backticks but keep content.
    text = text.replace("`", "")
    # Bold/italic markers.
    text = re.sub(r"(\*\*|\*|__|_)", "", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Links.
# --------------------------------------------------------------------------- #

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
CODE_PATH_RE = re.compile(r"^[\w./\-]+\.[A-Za-z0-9]+$")
# Media/asset extensions are classified as "other" (images etc.), never code_ref.
NON_CODE_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".pdf", ".mp4", ".mov", ".webm", ".mp3", ".wav", ".zip", ".tar", ".gz",
)


def _looks_like_code_ref(path):
    """A local non-md path that looks like a code file (not a media asset)."""
    low = path.lower()
    if low.endswith(MD_EXTS) or low.endswith(NON_CODE_EXTS):
        return False
    return bool(CODE_PATH_RE.match(path))


def _normalize_relative_target(target, source_rel, root):
    """ROOT-normalize a relative link target.

    ``target`` is resolved relative to the source file's directory, then made
    relative to ROOT so it can be matched against other files' ``file_path``.
    Returns the ROOT-relative POSIX path (no leading ``./``), or None if the
    target escapes ROOT.
    """
    target = target.split("#", 1)[0].split("?", 1)[0]
    if target == "":
        return None
    source_dir = os.path.dirname(source_rel)
    joined = os.path.normpath(os.path.join(source_dir, target))
    # Reject paths that climb above ROOT.
    if joined == ".." or joined.startswith(".." + os.sep):
        return None
    norm = joined.replace(os.sep, "/")
    if norm == ".":
        return None
    return norm


def parse_links(body, source_rel, root):
    """Extract and classify outbound links from a markdown body.

    Returns ``[{raw, target, type}]``; ``relative_md`` targets are
    ROOT-normalized. ``resolved_slug`` is filled later by the graph join.
    """
    links = []

    for m in LINK_RE.finditer(body):
        raw = m.group(0)
        url = m.group(2).strip()
        link = _classify_link(raw, url, source_rel, root)
        if link is not None:
            links.append(link)

    # Bare inline code that looks like a code path -> code_ref.
    for m in INLINE_CODE_RE.finditer(body):
        token = m.group(1).strip()
        if _looks_like_code_ref(token):
            links.append({
                "raw": m.group(0),
                "target": token,
                "type": "code_ref",
            })

    return links


def _classify_link(raw, url, source_rel, root):
    """Classify a single markdown link into the link taxonomy."""
    low = url.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return {"raw": raw, "target": url, "type": "external_url"}
    if low.startswith("mailto:") or url.startswith("#"):
        return {"raw": raw, "target": url, "type": "other"}
    # Strip fragment/query for extension inspection.
    path_part = url.split("#", 1)[0].split("?", 1)[0]
    if path_part.lower().endswith(MD_EXTS):
        target = _normalize_relative_target(url, source_rel, root)
        if target is None:
            return {"raw": raw, "target": url, "type": "other"}
        return {"raw": raw, "target": target, "type": "relative_md"}
    # Non-md local reference that looks like a code file/path.
    if _looks_like_code_ref(path_part):
        return {"raw": raw, "target": path_part, "type": "code_ref"}
    return {"raw": raw, "target": url, "type": "other"}


# --------------------------------------------------------------------------- #
# Language detection.
# --------------------------------------------------------------------------- #

def detect_language(body):
    """Detect the primary prose language (ISO-639-1 where possible).

    Stdlib-only heuristic over Unicode script ranges. Defaults to English
    ("en") for Latin-script prose, which dominates the target corpora
    (skills/specs/RCA notes).
    """
    counts = Counter()
    for ch in body:
        o = ord(ch)
        if 0x0400 <= o <= 0x04FF:
            counts["ru"] += 1          # Cyrillic
        elif 0x4E00 <= o <= 0x9FFF:
            counts["zh"] += 1          # CJK Unified Ideographs
        elif 0x3040 <= o <= 0x30FF:
            counts["ja"] += 1          # Hiragana + Katakana
        elif 0xAC00 <= o <= 0xD7A3:
            counts["ko"] += 1          # Hangul
        elif 0x0600 <= o <= 0x06FF:
            counts["ar"] += 1          # Arabic
        elif 0x0590 <= o <= 0x05FF:
            counts["he"] += 1          # Hebrew
        elif 0x0370 <= o <= 0x03FF:
            counts["el"] += 1          # Greek
        elif ch.isalpha() and o < 0x250:
            counts["en"] += 1          # Latin (incl. Latin-1 supplement)
    if not counts:
        return "en"
    # Prefer a non-Latin script if it accounts for a meaningful share.
    total = sum(counts.values())
    best, best_n = counts.most_common(1)[0]
    if best == "en":
        # Check if a non-Latin script is substantial enough to override.
        non_latin = [(k, v) for k, v in counts.items() if k != "en"]
        if non_latin:
            k, v = max(non_latin, key=lambda kv: kv[1])
            if v / total >= 0.20:
                return k
        return "en"
    return best


def first_h1_title(headings, rel_path):
    """Title = first H1 text, else humanized filename."""
    for h in headings:
        if h["level"] == 1:
            return h["text"]
    return humanize_filename(rel_path)


# --------------------------------------------------------------------------- #
# Graph.
# --------------------------------------------------------------------------- #

def _name_key(rel_path):
    """Bare filename (no extension, lowercased) used for weak name matching."""
    base = os.path.basename(rel_path)
    base = re.sub(r"\.(md|markdown)$", "", base, flags=re.IGNORECASE)
    return base.strip().lower()


def build_graph(files):
    """Build nodes + strong/weak edges and fill each link's ``resolved_slug``.

    - strong = a ``relative_md`` link whose ROOT-normalized target matches
      another node's ``file_path``.
    - weak   = a name match between two distinct files (marked tentative),
      emitted only when no strong edge already connects the pair.
    """
    path_to_slug = {f["file_path"]: f["slug"] for f in files}
    path_to_title = {f["file_path"]: f["title"] for f in files}

    nodes = [
        {"slug": f["slug"], "title": f["title"], "file_path": f["file_path"]}
        for f in files
    ]

    edges = []
    strong_pairs = set()

    # Strong edges + resolved_slug join.
    for f in files:
        src_slug = f["slug"]
        for link in f["links"]:
            if link["type"] != "relative_md":
                continue
            target = link["target"]
            if target in path_to_slug:
                tgt_slug = path_to_slug[target]
                link["resolved_slug"] = tgt_slug
                if tgt_slug != src_slug:
                    pair = (src_slug, tgt_slug)
                    if pair not in strong_pairs:
                        strong_pairs.add(pair)
                        edges.append({
                            "from": src_slug,
                            "to": tgt_slug,
                            "strength": "strong",
                            "reason": "direct relative link",
                        })

    # Weak edges by bare-name match (tentative), skipping pairs already strong.
    name_to_slugs = {}
    for f in files:
        name_to_slugs.setdefault(_name_key(f["file_path"]), []).append(f["slug"])

    slug_to_name = {f["slug"]: _name_key(f["file_path"]) for f in files}
    weak_seen = set()
    for f in files:
        src_slug = f["slug"]
        for link in f["links"]:
            if link["type"] != "relative_md":
                continue
            if "resolved_slug" in link:
                continue  # already a strong, resolved edge
            # Name match against another file by the link's bare target name.
            tgt_name = _name_key(link["target"])
            for cand_slug in name_to_slugs.get(tgt_name, []):
                if cand_slug == src_slug:
                    continue
                pair = (src_slug, cand_slug)
                if pair in strong_pairs or pair in weak_seen:
                    continue
                weak_seen.add(pair)
                edges.append({
                    "from": src_slug,
                    "to": cand_slug,
                    "strength": "weak",
                    "reason": f"name match: '{tgt_name}' (tentative)",
                })

    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# Groups (whole-corpus directory grouping; pure derivation from files[]).
# --------------------------------------------------------------------------- #

def build_groups(files):
    """Group files by their containing directory (restores v1 ``discover_md``).

    ``group`` = ``os.path.dirname(file_path)`` or ``"."`` for root-level files.
    Each group carries its files (sorted by ``file_path``) as a trimmed record
    ``{slug, file_path, title, token_estimate}``, the ``file_count``, and the
    summed ``token_estimate``. Groups are sorted by group name. Pure derivation
    from ``files[]`` — never touches meta/files/graph.
    """
    by_group = {}
    for f in files:
        group = os.path.dirname(f["file_path"]) or "."
        by_group.setdefault(group, []).append(f)

    groups = []
    for group in sorted(by_group):
        members = sorted(by_group[group], key=lambda f: f["file_path"])
        group_files = [
            {
                "slug": f["slug"],
                "file_path": f["file_path"],
                "title": f["title"],
                "token_estimate": f["token_estimate"],
            }
            for f in members
        ]
        groups.append({
            "group": group,
            "files": group_files,
            "file_count": len(group_files),
            "token_estimate": sum(f["token_estimate"] for f in group_files),
        })
    return groups


# --------------------------------------------------------------------------- #
# Main.
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Parse a Markdown corpus into structure.json")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--exclude", nargs="*", default=DEFAULT_EXCLUDES)
    ap.add_argument("--output-language", default="source")
    args = ap.parse_args()

    root = os.path.abspath(args.root)

    if not os.path.isdir(root):
        sys.stderr.write(f"Error: '{args.root}' is not a directory or does not exist.\n")
        sys.exit(1)
    if not os.access(root, os.R_OK | os.X_OK):
        sys.stderr.write(f"Error: '{args.root}' is not readable.\n")
        sys.exit(1)

    excludes = set(args.exclude)

    # Pass 1: collect file records (slug uniqueness applied in pass 2).
    records = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for fn in filenames:
            if not fn.lower().endswith(MD_EXTS):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
            try:
                with open(abs_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except UnicodeDecodeError:
                sys.stderr.write(f"Warning: skipping '{rel_path}' (not valid UTF-8).\n")
                continue
            except OSError:
                continue
            records.append((rel_path, text))

    records.sort(key=lambda x: x[0])

    # Pass 2: assign final slugs. Colliding base slugs -> ALL get a numeric
    # suffix (-1, -2, ...); no unnumbered first occurrence.
    base_slugs = [slugify_path(rel) for rel, _ in records]
    base_counts = Counter(base_slugs)
    seen = {}
    files = []
    for (rel_path, text), base in zip(records, base_slugs):
        if base_counts[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            slug = f"{base}-{seen[base]}"
        else:
            slug = base

        body = strip_frontmatter(text)
        headings = parse_headings(body)
        links = parse_links(body, rel_path, root)
        frontmatter = parse_frontmatter(text)
        title = first_h1_title(headings, rel_path)
        language = detect_language(body)

        record = {
            "slug": slug,
            "file_path": rel_path,
            "language": language,
            "title": title,
            "token_estimate": estimate_tokens(text),
            "token_estimate_method": "chars/4",
        }
        if frontmatter:
            record["frontmatter"] = frontmatter
        record["headings"] = headings
        record["links"] = links
        files.append(record)

    graph = build_graph(files)
    groups = build_groups(files)

    session = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root_label = os.path.relpath(root, os.getcwd()) if root != os.getcwd() else "."

    out = {
        "meta": {
            "session": session,
            "generated_at": generated_at,
            "root": root_label,
            "output_language": args.output_language,
            "file_count": len(files),
        },
        "files": files,
        "groups": groups,
        "graph": graph,
    }

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
