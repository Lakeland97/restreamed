#!/usr/bin/env python3
"""Extract UTF-16LE printable runs from a binary and filter for endpoints/keywords.

Usage: u16scan.py <file> [--all] [--min N]
Default: prints runs matching endpoint/keyword patterns.
"""
import re
import sys

KEYWORDS = re.compile(
    r"https?://|\.com|\.tv|\.net|\.org|\.io|\.gg|amazonaws|streamote|appserver|"
    r"gamelift|elasticbeanstalk|cloudfront|herokuapp|azure|playstreamline|"
    r"pgs|ec2|region|host|port|lobby|matchmak|/api/|login|server",
    re.I,
)
DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]{1,}\.[a-z]{2,}\b", re.I)


def runs(data: bytes, min_chars: int):
    # UTF-16LE runs of printable ASCII
    pat = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % min_chars)
    for m in pat.finditer(data):
        yield m.group().decode("utf-16le", errors="replace")


def main():
    path = sys.argv[1]
    show_all = "--all" in sys.argv
    min_chars = 6
    if "--min" in sys.argv:
        min_chars = int(sys.argv[sys.argv.index("--min") + 1])
    with open(path, "rb") as f:
        data = f.read()
    hits, domains = set(), set()
    for s in runs(data, min_chars):
        if DOMAIN.search(s):
            domains.add(s)
        if show_all or KEYWORDS.search(s):
            hits.add(s)
    print(f"== domain-containing runs ({len(domains)}):")
    for s in sorted(domains):
        print(" ", s)
    print(f"== keyword runs ({len(hits)}):")
    for s in sorted(hits):
        print(" ", s)


if __name__ == "__main__":
    main()
