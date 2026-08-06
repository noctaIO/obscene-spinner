#!/usr/bin/env python3
"""Keep README.md's tables honest.

The pack / animation / theme tables in the README are generated from spin.py's
registries. Adding a pack and forgetting to regenerate would leave the docs
quietly lying, so CI runs this.

    python3 tools/check_docs.py          # verify (exit 1 on drift)
    python3 tools/check_docs.py --write  # regenerate in place
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import spin  # noqa: E402

README = os.path.join(ROOT, "README.md")
BEGIN = "<!-- BEGIN GENERATED TABLES -->"
END = "<!-- END GENERATED TABLES -->"


def splice(text, tables):
    try:
        i = text.index(BEGIN)
        j = text.index(END)
    except ValueError:
        sys.exit("README.md is missing the %s / %s markers" % (BEGIN, END))
    return text[:i] + BEGIN + "\n\n" + tables + "\n\n" + text[j:]


def main():
    with open(README, encoding="utf-8") as f:
        current = f.read()
    wanted = splice(current, spin.docs_tables())
    if "--write" in sys.argv:
        if wanted != current:
            with open(README, "w", encoding="utf-8") as f:
                f.write(wanted)
            print("README.md tables regenerated")
        else:
            print("README.md tables already up to date")
        return
    if wanted != current:
        sys.exit("README.md tables are stale — run: "
                 "python3 tools/check_docs.py --write")
    print("README.md tables match the registry")


if __name__ == "__main__":
    main()
