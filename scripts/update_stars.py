#!/usr/bin/env python3
"""
update_stars.py — Refresh star counts for all repos in data/repos.json,
discover new harness engineering repos, and regenerate README.md.

Usage:
    python scripts/update_stars.py

Requires:
    pip install requests

Environment:
    GITHUB_TOKEN   GitHub personal access token (recommended — raises rate
                   limit from 60 to 5,000 req/hr; search API 30 req/min)
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from urllib.parse import urlsplit
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parent.parent
DATA_FILE   = REPO_ROOT / "data" / "repos.json"
README_FILE = REPO_ROOT / "README.md"

# ── GitHub API ────────────────────────────────────────────────────────────────
GITHUB_API  = "https://api.github.com/repos/{}"
HEADERS: dict[str, str] = {"Accept": "application/vnd.github+json"}
if token := os.getenv("GITHUB_TOKEN"):
    HEADERS["Authorization"] = f"Bearer {token}"

SLEEP_BETWEEN = 0.5   # seconds between REST requests
SEARCH_SLEEP  = 7     # seconds between Search API calls (≤30/min with token)
MIN_STARS     = 10    # minimum stars to keep / add a repo

# ── Search queries: category → list[query_string] ─────────────────────────────
SEARCH_QUERIES: dict[str, list[str]] = {
    "\U0001f916 Agent Harness Frameworks": [
        "topic:harness-engineering stars:>50",
        "topic:agent-harness stars:>50",
        '"agent harness" framework stars:>100',
        '"harness engineering" agent framework stars:>50',
        '"multi-agent harness" stars:>50',
    ],
    "\U0001f680 CI/CD & DevOps Platforms": [
        "topic:harness stars:>100 ci",
        "harness ci cd in:name stars:>50",
        "harness devops in:name stars:>50",
    ],
    "\U0001f4ca LLM Evaluation Harnesses": [
        "topic:evaluation-framework language-model stars:>100",
        "evaluation-harness in:name stars:>50",
        '"lm evaluation harness" stars:>100',
        '"evaluation harness" llm stars:>50',
        "benchmark harness llm in:description stars:>50",
    ],
    "\U0001f4da Harness Engineering Guides & Learning": [
        "harness-engineering in:name stars:>20",
        '"harness engineering" tutorial stars:>20',
        '"harness engineering" guide stars:>20',
        "learn harness engineering in:name stars:>20",
    ],
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits": [
        "topic:agent-memory harness stars:>30",
        "topic:agent-skills harness stars:>30",
        '"context harness" agent stars:>20',
        "agent skills memory harness in:description stars:>20",
    ],
    "\u26a1 Benchmarking & Testing Harnesses": [
        "topic:test-harness stars:>50",
        "test-harness in:name stars:>50",
        "benchmark-harness in:name stars:>30",
        '"test harness" framework stars:>100',
    ],
    "\U0001f510 Security & Fuzzing Harnesses": [
        "topic:fuzzing harness stars:>50",
        "fuzzing harness in:name stars:>50",
        "fuzz harness in:name stars:>30",
    ],
    "\U0001f4cb Awesome & Curated Lists": [
        "awesome-harness in:name stars:>30",
        "awesome harness engineering in:name stars:>10",
    ],
    "\U0001f527 Miscellaneous Harness Projects": [
        "harness in:name stars:>200",
        '"agent harness" in:description stars:>100',
    ],
}

# ── Category config ───────────────────────────────────────────────────────────
CATEGORY_ORDER = [
    "\U0001f916 Agent Harness Frameworks",
    "\U0001f680 CI/CD & DevOps Platforms",
    "\U0001f4ca LLM Evaluation Harnesses",
    "\U0001f4da Harness Engineering Guides & Learning",
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits",
    "\u26a1 Benchmarking & Testing Harnesses",
    "\U0001f510 Security & Fuzzing Harnesses",
    "\U0001f4cb Awesome & Curated Lists",
    "\U0001f527 Miscellaneous Harness Projects",
]

CAT_DESC = {
    "\U0001f916 Agent Harness Frameworks":
        "Core frameworks for building, running, and orchestrating AI agents with harness engineering principles — feedback loops, context management, and production-grade scaffolding.",
    "\U0001f680 CI/CD & DevOps Platforms":
        "Industrial-grade CI/CD platforms and DevOps tools with harness branding or harness-first architectures.",
    "\U0001f4ca LLM Evaluation Harnesses":
        "Frameworks and tools for evaluating LLMs, agents, and AI systems at scale — benchmarking, grading, and leaderboards.",
    "\U0001f4da Harness Engineering Guides & Learning":
        "Tutorials, guides, playbooks, and educational resources on harness engineering principles and practices.",
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits":
        "Libraries and toolkits for managing agent memory, skills, context, and execution environments.",
    "\u26a1 Benchmarking & Testing Harnesses":
        "Test frameworks, benchmark harnesses, and quality assurance tools for software and AI systems.",
    "\U0001f510 Security & Fuzzing Harnesses":
        "Security research tools, fuzzing harnesses, and vulnerability research frameworks.",
    "\U0001f4cb Awesome & Curated Lists":
        "Curated awesome lists focused on harness engineering resources, tools, and projects.",
    "\U0001f527 Miscellaneous Harness Projects":
        "Other notable harness-related projects spanning specialized runtimes, domain-specific tools, and unique applications.",
}

TOC_ANCHORS = {
    "\U0001f916 Agent Harness Frameworks":              "agent-harness-frameworks",
    "\U0001f680 CI/CD & DevOps Platforms":              "cicd--devops-platforms",
    "\U0001f4ca LLM Evaluation Harnesses":              "llm-evaluation-harnesses",
    "\U0001f4da Harness Engineering Guides & Learning": "harness-engineering-guides--learning",
    "\U0001f6e0\ufe0f Skills, Memory & Context Toolkits": "skills-memory--context-toolkits",
    "\u26a1 Benchmarking & Testing Harnesses":          "benchmarking--testing-harnesses",
    "\U0001f510 Security & Fuzzing Harnesses":          "security--fuzzing-harnesses",
    "\U0001f4cb Awesome & Curated Lists":               "awesome--curated-lists",
    "\U0001f527 Miscellaneous Harness Projects":        "miscellaneous-harness-projects",
}


# ── GitHub Search helpers ──────────────────────────────────────────────────────
MAX_ATTEMPTS = 3
MAX_WAIT = 10


def canonical_url(value: str) -> str:
    """Accept repository URLs only; no credentials, ports or URL query syntax."""
    if not isinstance(value, str):
        raise ValueError("repository URL must be text")
    parsed = urlsplit(value)
    if (parsed.scheme.lower() != "https" or parsed.netloc.lower() != "github.com"
            or parsed.query or parsed.fragment):
        raise ValueError("expected HTTPS GitHub repository URL")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in (".", "..") for p in parts):
        raise ValueError("expected owner/repository")
    parts[1] = parts[1].removesuffix(".git")
    if not parts[1] or parts[1] in (".", ".."):
        raise ValueError("invalid repository")
    return "https://github.com/" + "/".join(parts).lower()


def deduplicate(repos: list[dict]) -> list[dict]:
    result, seen = [], set()
    for repo in repos:
        key = canonical_url(repo["url"])
        if key in seen:
            continue
        seen.add(key)
        result.append({**repo, "url": key, "name": key.removeprefix("https://github.com/")})
    return result


def bounded_get(url, **kwargs):
    """At most three requests and two bounded sleeps; authorization 403 is final."""
    for attempt in range(MAX_ATTEMPTS):
        response = requests.get(url, headers=HEADERS, timeout=15,
                                allow_redirects=False, **kwargs)
        rate_limited = response.status_code == 403 and (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "Retry-After" in response.headers)
        retryable = rate_limited or response.status_code in (429, 500, 502, 503, 504)
        if not retryable or attempt == MAX_ATTEMPTS - 1:
            return response
        try:
            raw = response.headers.get("Retry-After")
            if raw is not None:
                delay = float(raw)
            elif rate_limited and "X-RateLimit-Reset" in response.headers:
                delay = float(response.headers["X-RateLimit-Reset"]) - time.time()
            else:
                delay = 2 ** attempt
        except (TypeError, ValueError):
            delay = 2 ** attempt
        # Do not retry early when the server asks us to wait beyond our budget.
        if not math.isfinite(delay) or delay > MAX_WAIT:
            return response
        time.sleep(max(0, delay))
    raise AssertionError("unreachable")


def search_github(query: str) -> list[dict]:
    """Failures abort the update instead of publishing a partial success."""
    response = bounded_get("https://api.github.com/search/repositories",
                           params={"q": query, "sort": "stars", "order": "desc", "per_page": 100})
    if response.status_code != 200:
        raise RuntimeError(f"GitHub search failed: HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or payload.get("incomplete_results"):
        raise ValueError("invalid or incomplete search result")
    return payload["items"]


def normalize_repo(gh_item: dict, category: str) -> dict:
    """Convert a GitHub Search API result item to the internal schema."""
    return {
        "name":        canonical_url(gh_item["html_url"]).removeprefix("https://github.com/"),
        "url":         canonical_url(gh_item["html_url"]),
        "description": gh_item.get("description") or "",
        "stars":       gh_item["stargazers_count"],
        "forks":       gh_item.get("forks_count", 0),
        "language":    gh_item.get("language") or "",
        "topics":      ",".join(gh_item.get("topics") or []),
        "updated":     gh_item.get("updated_at", ""),
        "category":    category,
    }


def discover_repos(existing_urls: set[str]) -> list[dict]:
    """
    Run all SEARCH_QUERIES; return newly-discovered repos not in
    *existing_urls* and with stars > MIN_STARS.
    """
    existing_urls = {canonical_url(url) for url in existing_urls}
    new_repos: list[dict] = []
    for category, queries in SEARCH_QUERIES.items():
        for query in queries:
            print(f"  Searching: {query!r}")
            items = search_github(query)
            for item in items:
                html_url = canonical_url(item.get("html_url", ""))
                if html_url in existing_urls:
                    continue
                if item.get("stargazers_count", 0) <= MIN_STARS:
                    continue
                new_repos.append(normalize_repo(item, category))
                existing_urls.add(html_url)
            time.sleep(SEARCH_SLEEP)
    print(f"Discovered {len(new_repos)} new repos")
    return new_repos


# ── Star fetching ──────────────────────────────────────────────────────────────
def fetch_stars(full_name: str) -> int | None:
    """Return stars or None; main refuses publication if any refresh failed."""
    name = canonical_url("https://github.com/" + full_name).removeprefix("https://github.com/")
    try:
        response = bounded_get(GITHUB_API.format(name))
        if response.status_code == 200:
            stars = response.json().get("stargazers_count")
            if type(stars) is int and stars >= 0:
                return stars
    except (requests.RequestException, ValueError, AttributeError):
        pass
    return None


def atomic_write(path: Path, content: str) -> None:
    """Replace one file atomically in its own directory; not a two-file transaction."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".catalog-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# ── README generation ──────────────────────────────────────────────────────────
def fmt_row(r: dict) -> str:
    name  = r["name"]
    url   = r["url"]
    stars = r["stars"]
    lang  = f"`{r['language']}`" if r.get("language") else ""
    desc  = (r.get("description") or "").replace("|", "\\|")
    if len(desc) > 120:
        desc = desc[:117] + "..."
    return f"| [**{name}**]({url}) | ⭐ {stars:,} | {lang} | {desc} |"


def generate_readme(repos_by_cat: dict[str, list[dict]], today: str) -> str:
    all_repos = [r for cat in CATEGORY_ORDER for r in repos_by_cat.get(cat, [])]
    total     = len(all_repos)
    num_cats  = sum(1 for cat in CATEGORY_ORDER if repos_by_cat.get(cat))

    lang_counter = Counter(r["language"] for r in all_repos if r.get("language"))
    top_langs    = ", ".join(f"{l}({c})" for l, c in lang_counter.most_common(8))

    # Deduplicated top 10
    seen: set[str] = set()
    top10: list[dict] = []
    for r in sorted(all_repos, key=lambda x: -x["stars"]):
        if r["url"] not in seen:
            seen.add(r["url"])
            top10.append(r)
        if len(top10) == 10:
            break

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    today_badge = today.replace("-", "--")
    lines += [
        '<div align="center">',
        "  <h1>Awesome Harness Engineering</h1>",
        "  <p>A curated list of awesome harness engineering frameworks, libraries, tools, and resources for building reliable AI agent systems.</p>",
        "",
        "  [![Awesome](https://awesome.re/badge.svg)](https://awesome.re)",
        "  ![GitHub stars](https://img.shields.io/github/stars/yenanjing/awesome-harness-engineering?style=flat-square)",
        f"  ![Last Updated](https://img.shields.io/badge/last%20updated-{today_badge}-blue?style=flat-square)",
        "",
        f"  <p>Collected <strong>{total}</strong> repositories across <strong>{num_cats}</strong> categories covering the harness engineering ecosystem.</p>",
        "</div>",
        "",
        "---",
        "",
        "## 📖 Table of Contents",
        "",
        "- [About](#-about)",
        "",
    ]
    for cat in CATEGORY_ORDER:
        if repos_by_cat.get(cat):
            anchor = TOC_ANCHORS.get(cat, "")
            lines.append(f"- [{cat}](#{anchor})")
    lines += [
        "- [📊 Stats](#-stats)",
        "- [⭐ Star History](#-star-history)",
        "- [🤝 Contributing](#-contributing)",
        "",
        "---",
        "",
        "## 🌟 About",
        "",
        "**Harness Engineering** is a discipline focused on designing the environment, constraints, feedback loops, and tooling that allow AI agents to operate reliably, verifiably, and safely in production. It goes beyond writing prompts — it's about shaping the scaffolding, context management, memory systems, evaluation pipelines, and guardrails that keep AI systems on track.",
        "",
        "This list curates the best open-source projects in the harness engineering ecosystem:",
        "",
        "- 🤖 **Agent harness frameworks** — multi-agent orchestration, skill systems, and production runtimes",
        "- 🚀 **CI/CD & DevOps platforms** — harness-first build and deployment pipelines",
        "- 📊 **LLM evaluation harnesses** — benchmarking and grading frameworks",
        "- 📚 **Guides & learning resources** — tutorials, playbooks, and educational content",
        "- 🛠️ **Skills, memory & context toolkits** — context management and long-term memory",
        "- ⚡ **Test & benchmark harnesses** — quality assurance and performance frameworks",
        "- 🔐 **Security & fuzzing harnesses** — vulnerability research and chaos engineering",
        "",
        f"> Last updated: {today}",
        "",
        "---",
        "",
    ]

    # ── Category sections ─────────────────────────────────────────────────────
    for cat in CATEGORY_ORDER:
        repos = repos_by_cat.get(cat, [])
        if not repos:
            continue
        desc = CAT_DESC.get(cat, "")
        lines += [
            f"## {cat}",
            "",
            f"> {desc}",
            "",
            "| Repository | Stars | Language | Description |",
            "|-----------|-------|----------|-------------|",
        ]
        for r in repos:
            lines.append(fmt_row(r))
        lines += ["", "---", ""]

    # ── Stats ─────────────────────────────────────────────────────────────────
    lines += [
        "## 📊 Stats",
        "",
        f"- **Total repositories**: {total}",
        f"- **Categories**: {num_cats}",
        f"- **Top languages**: {top_langs}",
        f"- **Last updated**: {today}",
        "",
        "### 🏆 Top 10 by Stars",
        "",
        "| Rank | Repository | Stars | Description |",
        "|------|-----------|-------|-------------|",
    ]
    for i, r in enumerate(top10, 1):
        desc = (r.get("description") or "").replace("|", "\\|")
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"| {i} | [{r['name']}]({r['url']}) | ⭐ {r['stars']:,} | {desc} |")

    lines += [
        "",
        "---",
        "",
        "## ⭐ Star History",
        "",
        "[![Star History Chart](https://api.star-history.com/svg?repos=yenanjing/awesome-harness-engineering&type=Date)](https://star-history.com/#yenanjing/awesome-harness-engineering&Date)",
        "",
        "---",
        "",
        "## 🤝 Contributing",
        "",
        "Contributions are welcome! Please read the [contribution guidelines](CONTRIBUTING.md) first.",
        "",
        "To add a project:",
        "1. Fork this repository",
        "2. Add your project to the relevant section",
        "3. Ensure it has 1,000+ stars (or is highly relevant with 100+ stars) and is actively maintained",
        "4. Submit a Pull Request",
        "",
        "---",
        "",
        "## 📄 License",
        "",
        "[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](https://creativecommons.org/publicdomain/zero/1.0/)",
        "",
        "This list is under the [CC0 1.0](LICENSE) license.",
        "",
        "---",
        "",
        '<div align="center">',
        '  <sub>Generated with ❤️ using <a href="https://claude.ai/claude-code">Claude Code</a> | Auto-updated via GitHub Actions</sub>',
        "</div>",
    ]
    return "\n".join(lines) + "\n"


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading {DATA_FILE}")
    with DATA_FILE.open() as f:
        repos: list[dict] = deduplicate(json.load(f))

    total = len(repos)
    print(f"  {total} repos to refresh")

    updated = skipped = errors = 0
    for i, repo in enumerate(repos, 1):
        full_name = repo["name"]
        new_stars = fetch_stars(full_name)
        if new_stars is None:
            errors += 1
        elif new_stars != repo["stars"]:
            print(f"  [{i}/{total}] {full_name}: {repo['stars']:,} → {new_stars:,}")
            repo["stars"] = new_stars
            updated += 1
        else:
            skipped += 1
        time.sleep(SLEEP_BETWEEN)

    print(f"\nRefresh done: {updated} updated, {skipped} unchanged, {errors} errors")

    if errors:
        raise RuntimeError("refresh incomplete; no output files changed")

    # Drop repos below threshold
    before = len(repos)
    repos = [r for r in repos if r.get("stars", 0) > MIN_STARS]
    print(f"Dropped {before - len(repos)} repos with ≤{MIN_STARS} stars ({len(repos)} remain)")

    # Discover new repos
    print("\n--- Discovering new repos ---")
    existing_urls: set[str] = {r["url"] for r in repos}
    new_repos = discover_repos(existing_urls)
    repos.extend(new_repos)
    print(f"Total repos after discovery: {len(repos)}")

    # Group by category and sort
    repos_by_cat: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_ORDER}
    for repo in repos:
        cat = repo.get("category", "")
        if cat in repos_by_cat and repo["stars"] > MIN_STARS:
            repos_by_cat[cat].append(repo)
    for cat in repos_by_cat:
        repos_by_cat[cat].sort(key=lambda x: -x["stars"])

    today = date.today().isoformat()
    readme = generate_readme(repos_by_cat, today)
    # Serialize both before replacing either; each replacement is atomic.
    serialized = json.dumps(repos, indent=2, ensure_ascii=False) + "\n"
    atomic_write(DATA_FILE, serialized)
    atomic_write(README_FILE, readme)
    print(f"Saved {README_FILE}")


if __name__ == "__main__":
    main()
