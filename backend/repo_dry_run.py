"""
GitHub Repository Scanner — dry run
-----------------------------------
Scans a public GitHub repository's tree without cloning it or downloading
file contents. Classifies files and reports which paths would be analyzed.

Usage:
    pip install requests
    python repo_dry_run.py https://github.com/psf/requests
    python repo_dry_run.py https://github.com/owner/repo --max-files 20000
    python repo_dry_run.py https://github.com/owner/repo --token YOUR_GITHUB_TOKEN

No repository files are changed. The script does not parse source code yet.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import urlparse

import requests

API_BASE = "https://api.github.com"
TIMEOUT_SECONDS = 30

# Global path components ignored regardless of language.
GLOBAL_IGNORED_DIRS = {
    ".git", ".idea", ".vscode", ".cache", "coverage",
    "tmp", "temp", "logs",
}

# Build output directories. "target" is especially common in Java/Maven.
GLOBAL_BUILD_DIRS = {"build", "dist", "out", "target", "bin", "obj"}

LANGUAGE_IGNORED_DIRS = {
    "python": {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".hypothesis", ".tox", ".nox", ".venv", "venv", "env", "ENV",
        "site-packages", "htmlcov",
    },
    "java": {
        ".gradle", ".gradle-cache", ".settings",
    },
    "javascript_typescript": {
        "node_modules", ".next", ".nuxt", ".nitro", ".svelte-kit",
        ".angular", ".expo", ".parcel-cache", ".vite", ".turbo",
        "storybook-static",
    },
}

# Files that are usually generated/compiled or not useful to parse as source.
GLOBAL_IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear", ".node",
    ".dll", ".exe", ".so", ".dylib", ".o", ".a", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".7z", ".mp4", ".mp3", ".woff", ".woff2", ".ttf", ".otf",
}

SOURCE_EXTENSIONS = {
    ".py": "Python",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
}

CONFIG_FILENAMES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "yarn.lock", "pnpm-lock.yaml", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "gradle.properties", "requirements.txt", "pyproject.toml",
    "setup.py", "setup.cfg", "pipfile", "pipfile.lock",
    "poetry.lock", "uv.lock", "tox.ini", "tsconfig.json",
    "jsconfig.json", "webpack.config.js", "vite.config.ts",
    "vite.config.js", "babel.config.js", ".babelrc",
}

DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
MINIFIED_PATTERNS = (
    re.compile(r"\.min\.(?:js|css)$", re.IGNORECASE),
)

def parse_github_url(url: str) -> tuple[str, str]:
    """Accept https://github.com/owner/repo, optionally with trailing slash."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "github.com", "www.github.com"
    }:
        raise ValueError("Please provide a URL like https://github.com/owner/repo")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("The URL must include both owner and repository name.")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def get_json(session: requests.Session, url: str, params=None):
    response = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
    if response.status_code == 404:
        raise RuntimeError(
            "Repository/tree not found or private. Check the URL and access permissions."
        )
    if response.status_code == 403:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        detail = response.text[:300]
        raise RuntimeError(
            f"GitHub returned 403 (rate limit or permission issue). "
            f"Remaining={remaining}, reset_epoch={reset}. Details: {detail}"
        )
    response.raise_for_status()
    return response.json()


def path_language(path: str) -> str | None:
    suffix = os.path.splitext(path)[1].lower()
    lang = SOURCE_EXTENSIONS.get(suffix)
    if lang == "Python":
        return "python"
    if lang == "Java":
        return "java"
    if lang in {"JavaScript", "TypeScript"}:
        return "javascript_typescript"
    return None


def ignored_reason(path: str, file_size: int | None) -> str | None:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    lower_parts = [p.lower() for p in parts]
    filename = parts[-1]
    lower_name = filename.lower()
    lang = path_language(path)

    # Ignore .git anywhere in the path; generated/vendor dirs are path-segment based.
    if ".git" in lower_parts:
        return "GIT_METADATA"

    if any(p in GLOBAL_IGNORED_DIRS for p in lower_parts):
        return "GLOBAL_TOOL_CACHE_OR_TEMP"

    if any(p in GLOBAL_BUILD_DIRS for p in lower_parts):
        return "BUILD_OUTPUT"

    if lang:
        lang_dirs = LANGUAGE_IGNORED_DIRS[lang]
        if any(p in lang_dirs for p in lower_parts):
            if "node_modules" in lower_parts:
                return "VENDOR_NODE_MODULES"
            if pythons := ({"__pycache__", ".pytest_cache", ".mypy_cache",
                            ".ruff_cache", ".hypothesis", ".tox", ".nox",
                            ".venv", "venv", "env", "ENV", "site-packages",
                            "htmlcov"} & set(parts)):
                return "PYTHON_ENV_OR_CACHE"
            if ".gradle" in lower_parts or ".gradle-cache" in lower_parts:
                return "JAVA_BUILD_CACHE"
            return "FRAMEWORK_CACHE_OR_GENERATED"

    if any(pattern.search(lower_name) for pattern in MINIFIED_PATTERNS):
        return "MINIFIED_ASSET"

    suffix = os.path.splitext(lower_name)[1]
    if suffix in GLOBAL_IGNORED_SUFFIXES:
        return "BINARY_OR_COMPILED"

    # Avoid accidentally processing very large individual source files.
    if file_size is not None and file_size > 1_000_000:
        return "FILE_TOO_LARGE_OVER_1MB"

    return None


def classify(path: str, file_size: int | None) -> dict:
    reason = ignored_reason(path, file_size)
    suffix = os.path.splitext(path)[1].lower()
    lower_name = path.rsplit("/", 1)[-1].lower()

    if reason:
        return {"status": "IGNORED", "category": "ignored", "reason": reason}

    lang = SOURCE_EXTENSIONS.get(suffix)
    if lang:
        return {"status": "INCLUDED", "category": "source", "language": lang}

    if lower_name in CONFIG_FILENAMES:
        return {"status": "INCLUDED", "category": "config", "language": None}

    if suffix in DOC_EXTENSIONS or lower_name in {"readme", "license", "changelog"}:
        return {"status": "SKIPPED", "category": "documentation", "reason": "NOT_SOURCE"}

    # GitHub tree entries may include extensionless scripts; don't guess they are source.
    if not suffix:
        return {"status": "SKIPPED", "category": "unknown", "reason": "NO_EXTENSION"}

    return {"status": "SKIPPED", "category": "other", "reason": "UNSUPPORTED_FILE_TYPE"}


def main():
    parser = argparse.ArgumentParser(description="Dry-run GitHub repository file scanner")
    parser.add_argument("repo_url", help="GitHub repository URL")
    parser.add_argument(
        "--token", default=os.getenv("GITHUB_TOKEN"),
        help="Optional GitHub token; alternatively set GITHUB_TOKEN environment variable",
    )
    parser.add_argument(
        "--max-files", type=int, default=100_000,
        help="Safety cap for tree entries to report (default: 100000)",
    )
    parser.add_argument(
        "--show", type=int, default=30,
        help="Maximum number of included source/config paths and ignored examples to print",
    )
    parser.add_argument(
        "--json", dest="json_path",
        help="Optional path to save the full dry-run report as JSON",
    )
    args = parser.parse_args()

    try:
        owner, repo = parse_github_url(args.repo_url)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        sys.exit(2)

    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "repo-graph-analyzer-dry-run/0.1",
    })
    if args.token:
        session.headers["Authorization"] = f"Bearer {args.token}"

    try:
        metadata = get_json(session, f"{API_BASE}/repos/{owner}/{repo}")
        default_branch = metadata.get("default_branch")
        if not default_branch:
            raise RuntimeError("GitHub did not return a default branch.")

        # Use the branch's commit SHA for the tree endpoint; this avoids ambiguity
        # when branch names contain slashes or unusual characters.
        branch_data = get_json(
            session, f"{API_BASE}/repos/{owner}/{repo}/branches/{requests.utils.quote(default_branch, safe='')}"
        )
        tree_sha = branch_data["commit"]["commit"]["tree"]["sha"]

        tree_data = get_json(
            session,
            f"{API_BASE}/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        print(f"GitHub API error: {exc}", file=sys.stderr)
        sys.exit(1)

    tree = tree_data.get("tree", [])
    truncated = bool(tree_data.get("truncated"))
    if len(tree) > args.max_files:
        tree = tree[:args.max_files]
        safety_truncated = True
    else:
        safety_truncated = False

    entries = []
    for item in tree:
        # Only file blobs; directories are implied by file paths.
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        result = classify(path, item.get("size"))
        entries.append({
            "path": path,
            "size": item.get("size"),
            "git_sha": item.get("sha"),
            **result,
        })

    status_counts = Counter(e["status"] for e in entries)
    category_counts = Counter(e["category"] for e in entries)
    reason_counts = Counter(
        e.get("reason") for e in entries if e["status"] == "IGNORED"
    )
    language_counts = Counter(
        e.get("language") for e in entries
        if e["status"] == "INCLUDED" and e["category"] == "source"
    )

    report = {
        "repository": metadata.get("full_name", f"{owner}/{repo}"),
        "html_url": metadata.get("html_url"),
        "default_branch": default_branch,
        "tree_truncated_by_github": truncated,
        "tree_truncated_by_local_safety_cap": safety_truncated,
        "tree_entries_returned": len(tree_data.get("tree", [])),
        "file_entries_classified": len(entries),
        "counts": {
            "status": dict(status_counts),
            "category": dict(category_counts),
            "source_language": dict(language_counts),
            "ignored_reason": dict(reason_counts),
        },
        "entries": entries,
        "notes": [
            "Dry run only: no file contents were fetched and no repository was cloned.",
            "This is a heuristic path/extension filter; inspect false positives/negatives.",
            "GitHub may mark a recursive tree as truncated. A truncated tree needs subtree traversal for complete coverage.",
            "Source files are identified by extension; the script does not parse imports or call relationships.",
        ],
    }

    print("\n" + "=" * 72)
    print("GITHUB REPOSITORY SCANNER — DRY RUN")
    print("=" * 72)
    print(f"Repository:       {report['repository']}")
    print(f"URL:              {report['html_url']}")
    print(f"Default branch:   {default_branch}")
    print(f"Tree entries:     {report['tree_entries_returned']}")
    print(f"Files classified: {len(entries)}")
    print(f"GitHub truncated: {truncated}")
    print(f"Local cap hit:    {safety_truncated}")

    print("\nStatus counts:")
    for key in ("INCLUDED", "IGNORED", "SKIPPED"):
        print(f"  {key:10} {status_counts.get(key, 0)}")

    print("\nIncluded source files by language:")
    if language_counts:
        for lang, count in sorted(language_counts.items()):
            print(f"  {lang:16} {count}")
    else:
        print("  No supported source files found.")

    print("\nIncluded configuration files:")
    print(f"  {sum(1 for e in entries if e['status'] == 'INCLUDED' and e['category'] == 'config')}")

    print("\nIgnored reasons:")
    if reason_counts:
        for reason, count in reason_counts.most_common():
            print(f"  {reason:36} {count}")
    else:
        print("  None")

    included = [e for e in entries if e["status"] == "INCLUDED"]
    ignored = [e for e in entries if e["status"] == "IGNORED"]

    print(f"\nFirst {args.show} included paths:")
    for e in included[:args.show]:
        extra = f" [{e.get('language')}]" if e.get("language") else f" [{e['category']}]"
        print(f"  + {e['path']}{extra}")

    print(f"\nFirst {args.show} ignored paths:")
    for e in ignored[:args.show]:
        print(f"  - {e['path']} [{e['reason']}]")

    if truncated or safety_truncated:
        print("\nWARNING: The scan may be incomplete. Implement recursive subtree fetching before relying on full-repository counts.")

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nFull report saved to: {args.json_path}")

    print("\nDry run complete. No repository content was downloaded or changed.")


if __name__ == "__main__":
    main()
