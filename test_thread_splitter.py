#!/usr/bin/env python3
"""Tests for thread_splitter.py. Run: python3 test_thread_splitter.py"""
import json
import subprocess
import sys
import tempfile
import os

PY = sys.executable
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thread_splitter.py")

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def run(args, input_text=None):
    r = subprocess.run([PY, SCRIPT] + args, input=input_text,
                       capture_output=True, text=True)
    return r


def posts_from_stdout(out):
    """Parse the default (header) output into (header, body) pairs."""
    posts = []
    cur_header, cur_lines = None, []
    for line in out.splitlines():
        if line.startswith("--- ") and "chars) ---" in line:
            if cur_header is not None:
                posts.append((cur_header, "\n".join(cur_lines).strip()))
            cur_header, cur_lines = line, []
        elif line.strip() == "" and cur_header is not None and not cur_lines:
            continue
        else:
            cur_lines.append(line)
    if cur_header is not None:
        posts.append((cur_header, "\n".join(cur_lines).strip()))
    return posts


# 1. Short text -> exactly 1 post, numbered 1/1, no over-limit.
r = run(["--text", "Hello world. This is a short post."])
posts = posts_from_stdout(r.stdout)
check("short text: 1 post", len(posts) == 1, r.stdout)
check("short text: numbered 1/1", posts and posts[0][1].startswith("1/1 "), posts[0][1] if posts else "")
check("short text: exit 0", r.returncode == 0)

# 2. Long English text -> all posts <= 280 X-chars, numbering consistent.
long_text = (
    "Building in public is the fastest way to learn. Every tool I ship teaches me "
    "something new about the problem, the users, and my own code. "
    "The first tool is always the hardest, because you have no audience yet and no "
    "feedback loop to lean on. "
    "So I start small: one single-file CLI that solves one real problem well. "
    "No framework, no dependencies, no config files. Just a script you can read in "
    "one sitting and run in one command. "
    "If it helps one person, it was worth shipping. If it helps ten, I keep going. "
    "That is the whole strategy: ship small, ship often, and let the work compound."
)
r = run(["--text", long_text])
posts = posts_from_stdout(r.stdout)
check("long text: multiple posts", len(posts) >= 3, f"got {len(posts)}")
ok_len = all("chars) ---" in h and int(h.split("(")[1].split()[0]) <= 280 for h, _ in posts)
check("long text: every post <= 280", ok_len)
nums = [b.split(" ")[0] for _, b in posts]
check("long text: numbering 1/N..N/N",
      nums == [f"{i+1}/{len(posts)}" for i in range(len(posts))], str(nums))
check("long text: exit 0", r.returncode == 0)
# Content preserved: joining the bodies (minus prefixes) should contain the sentences.
joined = " ".join(b.split(" ", 1)[1] for _, b in posts)
check("long text: no sentence lost",
      "Building in public" in joined and "let the work compound" in joined)

# 3. URL counts as 23: a 280-char post with a long URL still fits.
url = "https://example.com/a/very/long/path/that/keeps/going/on/and/on?x=1&y=2"
filler = "word " * 40  # 200 chars
text_with_url = f"Check this out {url} " + filler.strip() + " end."
r = run(["--text", text_with_url, "--json"])
data = json.loads(r.stdout)
check("url: json parses", isinstance(data, list) and len(data) >= 1)
check("url: all posts <= 280 by X-count",
      all(p["chars"] <= 280 for p in data), str([p["chars"] for p in data]))
check("url: raw len would exceed 280 (proves URL weighting)",
      len(text_with_url) > 280, f"raw len {len(text_with_url)}")

# 4. CJK text splits and stays within limit.
cjk = (
    "公开造工具是最快的学习方式。每发一件工具，我都能从问题、用户和自己的代码里学到新东西。"
    "第一件工具总是最难的，因为还没有观众，也没有反馈可以依靠。"
    "所以我从小处开始：一个单文件命令行工具，只解决一个真实问题，不用框架、不用依赖、不用配置文件。"
    "一个能一口气读完、一行命令跑起来的脚本。如果它帮到一个人，就值得发；如果帮到十个人，就继续做下去。"
    "这就是全部策略：小步快发，让工作复利增长。"
    "写代码也是一样：先让它跑起来，再让它跑得好，最后才考虑跑得快。"
    "过早优化是万恶之源，但从来不优化就是懒。真正的本事是知道什么时候该停下来重构，什么时候该继续往前冲。"
    "工具也是这样：先解决一个真实的问题，再考虑加功能。功能是长出来的，不是设计出来的。"
)
r = run(["--text", cjk])
posts = posts_from_stdout(r.stdout)
check("cjk: multiple posts", len(posts) >= 2, f"got {len(posts)}")
ok_len = all(int(h.split("(")[1].split()[0]) <= 280 for h, _ in posts)
check("cjk: every post <= 280", ok_len)
joined = "".join(b.split(" ", 1)[1] for _, b in posts)
check("cjk: no content lost", "公开造工具" in joined and "复利增长" in joined)

# 5. Very long single word -> hard cut, warning on stderr, still <= limit.
r = run(["--text", "x" * 600, "--limit", "100"])
posts = posts_from_stdout(r.stdout)
check("long word: split into pieces", len(posts) == 7, f"got {len(posts)}")
check("long word: every piece <= 100",
      all(int(h.split("(")[1].split()[0]) <= 100 for h, _ in posts))
check("long word: warning on stderr", "cut mid-word" in r.stderr, r.stderr)
check("long word: exit 0", r.returncode == 0)

# 6. --check mode.
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write("Short post one.\n\nShort post two, also fine.\n")
    good = f.name
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write("Short post.\n\n" + "y" * 300 + "\n")
    bad = f.name
r = run(["--check", good])
check("check: valid thread exit 0", r.returncode == 0, r.stdout + r.stderr)
r = run(["--check", bad])
check("check: over-limit thread exit 1", r.returncode == 1, r.stdout + r.stderr)
check("check: flags the over post", "OVER" in r.stdout, r.stdout)
os.unlink(good)
os.unlink(bad)

# 7. --plain output: posts separated by blank lines, no headers.
r = run(["--text", long_text, "--plain"])
check("plain: no --- headers", "---" not in r.stdout)
check("plain: blank-line separated", "\n\n" in r.stdout)
check("plain: first post numbered", r.stdout.startswith("1/"), r.stdout[:20])

# 8. --no-number: no 1/N prefix.
r = run(["--text", long_text, "--no-number", "--plain"])
check("no-number: no prefix", not r.stdout.startswith("1/"), r.stdout[:20])
check("no-number: starts with content", r.stdout.startswith("Building in public"), r.stdout[:30])

# 9. Exact boundary: text of exactly 280 X-chars (no numbering) -> 1 post.
exact = "a" * 280
r = run(["--text", exact, "--no-number", "--json"])
data = json.loads(r.stdout)
check("boundary: exactly 280 -> 1 post", len(data) == 1, str(len(data)))
check("boundary: chars == 280", data[0]["chars"] == 280, str(data[0]["chars"]))

# 10. Empty input -> exit 2.
r = run(["--text", "   "])
check("empty: exit 2", r.returncode == 2, f"got {r.returncode}")

# 11. File input + stdin input both work.
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write(long_text)
    fname = f.name
r = run([fname])
check("file input: works", r.returncode == 0 and "1/" in r.stdout)
r = run([], input_text=long_text)
check("stdin input: works", r.returncode == 0 and "1/" in r.stdout)
os.unlink(fname)

# 12. Custom --limit respected.
r = run(["--text", long_text, "--limit", "140", "--json"])
data = json.loads(r.stdout)
check("custom limit: all <= 140", all(p["chars"] <= 140 for p in data),
      str([p["chars"] for p in data]))
check("custom limit: more posts than 280", len(data) > 3, str(len(data)))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
