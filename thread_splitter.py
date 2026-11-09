#!/usr/bin/env python3
"""thread-splitter — split a long text into a numbered thread of short posts.

Takes a long text (a file, --text, or stdin) and cuts it into posts that fit
a character limit (default 280, X's limit). Splitting is sentence-aware:
it packs whole sentences into each post and only breaks a sentence when a
single sentence is longer than the limit.

Character counting follows X's rules closely enough to be useful:
  * every URL counts as 23 characters (X shortens links with t.co)
  * every other character counts as 1

Numbering ("1/5", "2/5", ...) is included in the count, so a post never
goes over the limit because of its own prefix.

Usage:
    python thread_splitter.py essay.txt
    python thread_splitter.py --text "Long text here..." --plain
    cat essay.txt | python thread_splitter.py --limit 500
    python thread_splitter.py --check thread.txt   # validate an existing thread

Options:
    --limit N      max characters per post (default 280)
    --no-number    don't add the "1/N" prefix
    --plain        print posts separated by a blank line, no headers
    --json         print a JSON list of posts with their character counts
    --check FILE   validate a thread file (posts separated by blank lines)
                   and report any post over the limit; exit code 1 if any

Exit codes: 0 ok, 1 --check found an over-limit post, 2 usage/input error.
"""

import argparse
import json
import re
import sys

DEFAULT_LIMIT = 280
URL_WEIGHT = 23  # X counts every link as 23 characters (t.co)

# A URL: http(s)://... up to the next whitespace.
URL_RE = re.compile(r"https?://\S+")

# Sentence-ending punctuation, Latin and CJK. A sentence ends when one of
# these is followed by whitespace (or the end of the text). The punctuation
# stays with the sentence it ends.
SENTENCE_END_RE = re.compile(
    r"(?<=[.!?…。！？])\s+"
)

# Characters that are safe to break a too-long sentence after.
BREAK_AFTER_RE = re.compile(r"[,;:、，；：]\s*")


def x_len(text):
    """Length of text the way X counts it: URLs weigh 23, the rest weigh 1."""
    total = 0
    pos = 0
    for m in URL_RE.finditer(text):
        total += len(text[pos:m.start()])
        total += URL_WEIGHT
        pos = m.end()
    total += len(text[pos:])
    return total


def split_sentences(paragraph):
    """Split one paragraph into sentences, keeping the punctuation."""
    parts = SENTENCE_END_RE.split(paragraph.strip())
    return [p.strip() for p in parts if p.strip()]


def hard_split(text, limit):
    """Split a too-long chunk into pieces of at most `limit` X-chars.

    Prefer to break after a comma/semicolon; otherwise break on a word
    boundary (Latin) or at the character limit (CJK has no spaces).
    A single word longer than the limit is cut mid-word and flagged.
    Returns (pieces, had_hard_cut).
    """
    pieces = []
    had_hard_cut = False
    rest = text.strip()
    while x_len(rest) > limit:
        # Find the best break point at or before the limit.
        cut = _best_break(rest, limit)
        if cut is None:
            # No clean break: cut exactly at the limit (mid-word).
            cut = _cut_at_limit(rest, limit)
            had_hard_cut = True
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces, had_hard_cut


def _best_break(text, limit):
    """Best index to cut `text` so the left part fits `limit` X-chars.

    Tries, in order: after a comma/semicolon, after a space. Returns None
    if no such break exists within the limit.
    """
    # Walk the text tracking X-length; record the last good break position.
    last_break = None
    i = 0
    n = len(text)
    while i < n:
        m = URL_RE.match(text, i)
        if m:
            width = URL_WEIGHT
            step = m.end() - i
        else:
            width = 1
            step = 1
        # Would adding this chunk exceed the limit?
        if _prefix_len(text, i) + width > limit and last_break is not None:
            return last_break
        if _prefix_len(text, i) + width > limit and last_break is None:
            return None
        # Record break points: after a comma/semicolon, or after a space.
        ch = text[i]
        if ch in ",;:、，；：":
            last_break = i + 1
        elif ch == " ":
            last_break = i + 1
        i += step
    return None


def _prefix_len(text, upto):
    """X-length of text[:upto]."""
    return x_len(text[:upto])


def _cut_at_limit(text, limit):
    """Index where text first exceeds `limit` X-chars (mid-word cut)."""
    total = 0
    i = 0
    n = len(text)
    while i < n:
        m = URL_RE.match(text, i)
        if m:
            width = URL_WEIGHT
            step = m.end() - i
        else:
            width = 1
            step = 1
        if total + width > limit:
            return i
        total += width
        i += step
    return n


def pack_sentences(sentences, limit):
    """Greedily pack sentences into posts of at most `limit` X-chars.

    Returns (posts, had_hard_cut). A single over-long sentence is split
    with hard_split(); a sentence that fits goes in whole.
    """
    posts = []
    had_hard_cut = False
    current = ""
    for sentence in sentences:
        if x_len(sentence) > limit:
            # Flush what we have, then hard-split the long sentence.
            if current:
                posts.append(current)
                current = ""
            pieces, cut = hard_split(sentence, limit)
            had_hard_cut = had_hard_cut or cut
            posts.extend(pieces)
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if x_len(candidate) <= limit:
            current = candidate
        else:
            posts.append(current)
            current = sentence
    if current:
        posts.append(current)
    return posts, had_hard_cut


def split_text(text, limit, numbered=True):
    """Split `text` into posts. Returns (posts, had_hard_cut)."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        return [], False

    if not numbered:
        all_posts = []
        had_hard_cut = False
        for para in paragraphs:
            posts, cut = pack_sentences(split_sentences(para), limit)
            had_hard_cut = had_hard_cut or cut
            all_posts.extend(posts)
        return all_posts, had_hard_cut

    # Numbered: the "i/N " prefix eats characters, and N is only known
    # after splitting. Iterate to a fixed point (2 passes is enough in
    # practice; cap at 5).
    prefix_width = 0
    posts = []
    had_hard_cut = False
    for _ in range(5):
        body_limit = limit - prefix_width
        if body_limit < 1:
            raise ValueError("limit too small for the numbering prefix")
        all_posts = []
        had_hard_cut = False
        for para in paragraphs:
            p, cut = pack_sentences(split_sentences(para), body_limit)
            had_hard_cut = had_hard_cut or cut
            all_posts.extend(p)
        n = len(all_posts)
        new_width = len(f"{n}/{n} ")
        if new_width == prefix_width:
            posts = all_posts
            break
        prefix_width = new_width
        posts = all_posts
    else:
        posts = all_posts

    n = len(posts)
    numbered_posts = [f"{i + 1}/{n} {p}" for i, p in enumerate(posts)]
    return numbered_posts, had_hard_cut


def read_input(args):
    """Read the source text from --text, a file, or stdin."""
    if args.text is not None:
        return args.text
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            print(f"thread-splitter: cannot read {args.file}: {e}", file=sys.stderr)
            sys.exit(2)
    if sys.stdin.isatty():
        print(
            "thread-splitter: no input — pass a file, --text, or pipe text in.",
            file=sys.stderr,
        )
        sys.exit(2)
    return sys.stdin.read()


def check_thread(path, limit):
    """Validate a thread file: posts separated by blank lines."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"thread-splitter: cannot read {path}: {e}", file=sys.stderr)
        return 2
    posts = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    bad = 0
    for i, post in enumerate(posts, 1):
        n = x_len(post)
        status = "OK " if n <= limit else "OVER"
        if n > limit:
            bad += 1
        print(f"{status} {i}/{len(posts)}: {n}/{limit} chars")
    if bad:
        print(f"{bad} post(s) over the {limit}-char limit.", file=sys.stderr)
        return 1
    print(f"All {len(posts)} post(s) fit within {limit} chars.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Split a long text into a numbered thread of short posts.",
    )
    parser.add_argument("file", nargs="?", help="text file to split (default: stdin)")
    parser.add_argument("--text", help="text to split, given on the command line")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"max characters per post (default {DEFAULT_LIMIT})")
    parser.add_argument("--no-number", action="store_true",
                        help="don't add the 1/N prefix")
    parser.add_argument("--plain", action="store_true",
                        help="print posts separated by a blank line, no headers")
    parser.add_argument("--json", action="store_true",
                        help="print a JSON list of posts with character counts")
    parser.add_argument("--check", metavar="FILE",
                        help="validate a thread file instead of splitting")
    args = parser.parse_args(argv)

    if args.limit < 10:
        print("thread-splitter: --limit must be at least 10.", file=sys.stderr)
        return 2

    if args.check:
        return check_thread(args.check, args.limit)

    text = read_input(args)
    if not text.strip():
        print("thread-splitter: input is empty.", file=sys.stderr)
        return 2

    try:
        posts, had_hard_cut = split_text(text, args.limit, numbered=not args.no_number)
    except ValueError as e:
        print(f"thread-splitter: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            [{"n": i + 1, "total": len(posts), "chars": x_len(p), "text": p}
             for i, p in enumerate(posts)],
            ensure_ascii=False, indent=2))
    elif args.plain:
        print("\n\n".join(posts))
    else:
        for i, post in enumerate(posts, 1):
            print(f"--- {i}/{len(posts)} ({x_len(post)} chars) ---")
            print(post)
            print()

    if had_hard_cut:
        print(
            "warning: a word or URL longer than the limit was cut mid-word.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
