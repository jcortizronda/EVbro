#!/usr/bin/env python3
"""Inline `style="…"` attributes → utility classes, so the page can ship `style-src` without `'unsafe-inline'`.

    python tools/inline_styles.py --check      # exit 1 if any static style="…" is left in index.html or the JS templates
    python tools/inline_styles.py --apply      # rewrite them into classes and regenerate css/base/utilities.css
    python tools/inline_styles.py --dry        # list what --apply would do

Rules of the generated classes (css/base/utilities.css, header says it is generated):
  * one class per distinct declaration list, named after it: `style="display: flex; gap: 0.5rem;"` → `u-display-flex_gap-0p5rem`
    (lower case, `:` → `-`, `;` → `_`, `.` → `p`, `%` → `pct`, anything else non-alphanumeric → `-`);
  * the selector is `.u-x:not(#u1):not(#u2):not(#u3)` (specificity 3,1,0): it beats every ordinary stylesheet rule, like the inline
    style did, but still loses to `!important` rules (the mobile layer uses some) and to `element.style.x = …` from the JS,
    exactly as an inline style would; no `!important` is generated;
  * dynamic values (`style="width:${pct}%"` in a template) are not touched: they must become `data-css="width:${pct}%"` plus
    `applyDataStyles(container)` (core/feedback.js), which sets them through the CSSOM, allowed by the policy.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "frontend" / "templates" / "index.html"
JS_DIRS = [ROOT / "frontend" / "static" / "js" / "core", ROOT / "frontend" / "static" / "js" / "features"]
OUT_CSS = ROOT / "frontend" / "static" / "css" / "base" / "utilities.css"

STYLE_ATTR = re.compile(r'\sstyle="([^"${}]*)"')               # static only: no template expressions inside
TAG = re.compile(r"<([a-zA-Z][a-zA-Z0-9-]*)(\s[^<>]*?)?(/?)>", re.S)
CLASS_ATTR = re.compile(r'\sclass="([^"]*)"')


def normalize(value: str) -> str:
    decls = []
    for part in value.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        prop, val = part.split(":", 1)
        decls.append((prop.strip().lower(), " ".join(val.split())))
    return "; ".join(f"{p}: {v}" for p, v in decls)


def class_name(normalized: str) -> str:
    parts = []
    for decl in normalized.split("; "):
        prop, val = decl.split(": ", 1)
        v = val.lower().replace(".", "p").replace("%", "pct")
        v = re.sub(r"[^a-z0-9]+", "-", v).strip("-")
        parts.append(f"{prop}-{v}")
    return "u-" + "_".join(parts)


SPECIFICITY = ":not(#u1):not(#u2):not(#u3)"      # three ids that never exist: the class wins like an inline style did


def css_rule(name: str, normalized: str) -> str:
    return f".{name}{SPECIFICITY} {{ {normalized}; }}"


def rewrite_tag(match: re.Match, registry: dict) -> str:
    name, attrs, slash = match.group(1), match.group(2) or "", match.group(3)
    m = STYLE_ATTR.search(attrs)
    if not m:
        return match.group(0)
    normalized = normalize(m.group(1))
    if not normalized:
        return f"<{name}{attrs[:m.start()] + attrs[m.end():]}{slash}>"
    cls = class_name(normalized)
    registry[cls] = normalized
    attrs = attrs[:m.start()] + attrs[m.end():]
    cm = CLASS_ATTR.search(attrs)
    if cm:
        classes = cm.group(1).split()
        if cls not in classes:
            classes.append(cls)
        attrs = attrs[:cm.start()] + f' class="{" ".join(classes)}"' + attrs[cm.end():]
    else:
        attrs = f' class="{cls}"' + attrs
    return f"<{name}{attrs}{slash}>"


def process(text: str, registry: dict) -> str:
    return TAG.sub(lambda m: rewrite_tag(m, registry), text)


def js_files():
    return sorted(p for d in JS_DIRS for p in d.glob("*.js"))


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    files = [HTML] + js_files()
    if args.check:
        left = []
        for f in files:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if STYLE_ATTR.search(" " + line):
                    left.append(f"{f.relative_to(ROOT)}:{i}")
        print("\n".join(left) if left else "✔ no static style=\"…\" left")
        return 1 if left else 0

    registry: dict = {}
    changed = {}
    for f in files:
        before = f.read_text(encoding="utf-8")
        after = process(before, registry)
        if after != before:
            changed[f] = after
    if args.dry:
        for f in changed:
            print("would rewrite", f.relative_to(ROOT))
        for cls, norm in sorted(registry.items()):
            print(f"  .{cls}  ←  {norm}")
        print(f"{len(registry)} classes, {len(changed)} files")
        return 0
    for f, after in changed.items():
        f.write_text(after, encoding="utf-8")
    existing = {}
    if OUT_CSS.exists():
        for m in re.finditer(r"^\.(u-[^ :]+)" + re.escape(SPECIFICITY) + r" \{ (.*) \}$", OUT_CSS.read_text(encoding="utf-8"), re.M):
            existing[m.group(1)] = m.group(2)
    rules = {**{k: css_rule(k, v) for k, v in registry.items()}}
    for k, body in existing.items():                     # keep classes that only live in JS-built HTML we did not see this run
        rules.setdefault(k, f".{k}{SPECIFICITY} {{ {body} }}")
    header = ("/* EVBro — Utility classes GENERATED by tools/inline_styles.py from the old inline style=\"…\" attributes.\n"
              "   Do not edit by hand: run `python tools/inline_styles.py --apply` again. The :not(#u1):not(#u2):not(#u3) selector\n"
              "   gives each class the priority the inline style had (beats every normal rule, loses to !important and element.style).\n"
              "   Standalone sheet: the load ORDER (templates/index.html) follows the original cascade; it goes last in base/. */\n")
    OUT_CSS.write_text(header + "\n".join(rules[k] for k in sorted(rules)) + "\n", encoding="utf-8")
    print(f"✔ {len(registry)} classes, {len(changed)} files rewritten, {OUT_CSS.relative_to(ROOT)} regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
