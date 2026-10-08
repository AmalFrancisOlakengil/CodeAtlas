from __future__ import annotations

import os
import re
import uuid
import base64
from typing import Optional, Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from tree_sitter import Language, Parser
import tree_sitter_python as ts_python
import tree_sitter_java as ts_java
import tree_sitter_javascript as ts_javascript
import tree_sitter_typescript as ts_typescript


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "http://localhost:3000",
)

BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "http://localhost:8000",
)

GITHUB_AUTHORIZE_URL = os.getenv(
    "GITHUB_AUTHORIZE_URL",
    "https://github.com/login/oauth/authorize",
)

GITHUB_ACCESS_TOKEN_URL = os.getenv(
    "GITHUB_ACCESS_TOKEN_URL",
    "https://github.com/login/oauth/access_token",
)

GITHUB_API_URL = os.getenv(
    "GITHUB_API_URL",
    "https://api.github.com",
)

MAX_FILE_SIZE = int(
    os.getenv("MAX_FILE_SIZE", "1000000")
)

DEFAULT_MAX_FILES = int(
    os.getenv("DEFAULT_MAX_FILES", "500")
)


if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
    print(
        "WARNING: GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET "
        "are not configured."
    )


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="CodeAtlas API",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# IN-MEMORY AUTHENTICATION SESSIONS
# ============================================================
#
# IMPORTANT:
#
# This dictionary stores ONLY authentication information.
#
# It does NOT store:
#   - repository graphs
#   - ASTs
#   - analysis results
#   - repository source code
#
# The graph is returned directly to the browser.
#
# For production with multiple backend instances, replace this
# with Redis or another shared session store.
# ============================================================

SESSIONS: dict[str, dict[str, Any]] = {}


# ============================================================
# TREE-SITTER
# ============================================================

LANGUAGES = {
    "python": Language(ts_python.language()),
    "java": Language(ts_java.language()),
    "javascript": Language(ts_javascript.language()),
    "typescript": Language(
        ts_typescript.language_typescript()
    ),
    "tsx": Language(
        ts_typescript.language_tsx()
    ),
}


def create_parser(language_name: str) -> Parser:
    parser = Parser()
    parser.language = LANGUAGES[language_name]
    return parser


# ============================================================
# FILE FILTERING
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


IGNORED_DIRECTORIES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".cache",
    "coverage",
    "tmp",
    "temp",
    "logs",
    "build",
    "dist",
    "out",
    "target",
    "bin",
    "obj",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "ENV",
    "site-packages",
    "htmlcov",
    ".gradle",
    ".gradle-cache",
    "node_modules",
    ".next",
    ".nuxt",
    ".nitro",
    ".svelte-kit",
    ".angular",
    ".expo",
    ".parcel-cache",
    ".vite",
    ".turbo",
    "storybook-static",
}


IGNORED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
    ".war",
    ".ear",
    ".dll",
    ".exe",
    ".so",
    ".dylib",
    ".o",
    ".a",
    ".node",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".mp3",
    ".mp4",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}


def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def extension(path: str) -> str:
    path = path.lower()

    if path.endswith(".min.js"):
        return ".min.js"

    index = path.rfind(".")

    if index == -1:
        return ""

    return path[index:]


def language_for_path(path: str) -> Optional[str]:
    ext = extension(path)

    return SUPPORTED_EXTENSIONS.get(ext)


def should_ignore(path: str) -> bool:
    normalized = normalize_path(path)

    parts = normalized.split("/")

    for part in parts[:-1]:
        if part in IGNORED_DIRECTORIES:
            return True

    filename = parts[-1].lower()

    if filename.endswith(".min.js"):
        return True

    for suffix in IGNORED_SUFFIXES:
        if filename.endswith(suffix):
            return True

    return False


# ============================================================
# TREE-SITTER HELPERS
# ============================================================

def node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8",
        errors="replace",
    )


def line_number(node) -> int:
    return node.start_point[0] + 1


def make_id(*parts: str) -> str:
    return "::".join(
        part.replace("\\", "/")
        for part in parts
        if part
    )


# ============================================================
# GRAPH
# ============================================================

class Graph:
    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        file: Optional[str] = None,
        line: Optional[int] = None,
        **extra,
    ):
        if node_id not in self.nodes:
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "name": name,
                "file": file,
                "line": line,
                **extra,
            }

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        line: Optional[int] = None,
    ):
        if not source or not target:
            return

        key = (
            source,
            target,
            edge_type,
        )

        if key not in self.edges:
            self.edges[key] = {
                "source": source,
                "target": target,
                "type": edge_type,
                "line": line,
            }

    def json(self):
        return {
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
        }


# ============================================================
# GITHUB CLIENT
# ============================================================

class GitHubClient:
    def __init__(self, token: str):
        self.token = token

        self.session = requests.Session()

        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "CodeAtlas/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ):
        response = self.session.request(
            method,
            url,
            timeout=30,
            **kwargs,
        )

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise HTTPException(
                status_code=response.status_code,
                detail={
                    "github_error": detail,
                },
            )

        return response

    def get_repository(
        self,
        owner: str,
        repo: str,
    ):
        response = self.request(
            "GET",
            f"{GITHUB_API_URL}/repos/{owner}/{repo}",
        )

        return response.json()

    def get_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
    ):
        response = self.request(
            "GET",
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params={
                "recursive": "1",
            },
        )

        return response.json()

    def get_blob(
        self,
        owner: str,
        repo: str,
        sha: str,
    ):
        response = self.request(
            "GET",
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/blobs/{sha}",
        )

        return response.json()


# ============================================================
# GITHUB URL
# ============================================================

def parse_github_url(repo_url: str):
    repo_url = repo_url.strip()

    pattern = (
        r"^https?://github\.com/"
        r"([^/]+)/([^/#?]+)"
        r"/?$"
    )

    match = re.match(pattern, repo_url)

    if not match:
        raise HTTPException(
            status_code=400,
            detail="Invalid GitHub repository URL.",
        )

    owner = match.group(1)
    repo = match.group(2)

    if repo.endswith(".git"):
        repo = repo[:-4]

    return owner, repo


# ============================================================
# SOURCE DECODING
# ============================================================

def safe_decode_blob(blob: dict) -> str:
    if blob.get("encoding") != "base64":
        return ""

    try:
        raw = base64.b64decode(
            blob.get("content", "")
        )

        if len(raw) > MAX_FILE_SIZE:
            return ""

        return raw.decode(
            "utf-8",
            errors="replace",
        )

    except Exception:
        return ""


# ============================================================
# SYMBOL EXTRACTION
# ============================================================

def extract_python(
    root,
    source: bytes,
    file_path: str,
    graph: Graph,
):
    symbols = []

    cursor = root.walk()

    def visit(node):
        node_type = node.type

        if node_type in (
            "function_definition",
            "async_function_definition",
        ):
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    "function",
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": "function",
                    "line": line_number(node),
                })

        elif node_type == "class_definition":
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    "class",
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": "class",
                    "line": line_number(node),
                })

        for child in node.children:
            visit(child)

    visit(root)

    # Imports
    for node in walk_tree(root):
        if node.type == "import_statement":
            text = node_text(
                node,
                source,
            )

            modules = re.findall(
                r"import\s+([a-zA-Z_][\w.]*)",
                text,
            )

            for module in modules:
                symbols.append({
                    "import": module,
                    "line": line_number(node),
                })

        elif node.type == "import_from_statement":
            text = node_text(
                node,
                source,
            )

            match = re.search(
                r"from\s+([a-zA-Z_][\w.]*)",
                text,
            )

            if match:
                symbols.append({
                    "import": match.group(1),
                    "line": line_number(node),
                })

    return symbols


def extract_java(
    root,
    source: bytes,
    file_path: str,
    graph: Graph,
):
    symbols = []

    for node in walk_tree(root):

        if node.type in (
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
        ):
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_type = {
                    "class_declaration": "class",
                    "interface_declaration": "interface",
                    "enum_declaration": "class",
                }[node.type]

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    node_type,
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": node_type,
                    "line": line_number(node),
                })

        elif node.type in (
            "method_declaration",
            "constructor_declaration",
        ):
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    "method",
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": "method",
                    "line": line_number(node),
                })

        elif node.type == "import_declaration":
            text = node_text(
                node,
                source,
            )

            text = re.sub(
                r"^\s*import\s+",
                "",
                text,
            )

            text = text.rstrip(";").strip()

            symbols.append({
                "import": text,
                "line": line_number(node),
            })

    return symbols


def extract_javascript(
    root,
    source: bytes,
    file_path: str,
    graph: Graph,
):
    symbols = []

    for node in walk_tree(root):

        if node.type in (
            "function_declaration",
            "generator_function_declaration",
        ):
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    "function",
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": "function",
                    "line": line_number(node),
                })

        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    "class",
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": "class",
                    "line": line_number(node),
                })

        elif node.type in (
            "method_definition",
        ):
            name_node = node.child_by_field_name("name")

            if name_node:
                name = node_text(
                    name_node,
                    source,
                )

                node_id = make_id(
                    file_path,
                    name,
                )

                graph.add_node(
                    node_id,
                    "method",
                    name,
                    file_path,
                    line_number(node),
                )

                symbols.append({
                    "id": node_id,
                    "name": name,
                    "type": "method",
                    "line": line_number(node),
                })

        elif node.type in (
            "import_statement",
            "import_clause",
        ):
            text = node_text(
                node,
                source,
            )

            match = re.search(
                r"from\s+['\"]([^'\"]+)['\"]",
                text,
            )

            if not match:
                match = re.search(
                    r"import\s+['\"]([^'\"]+)['\"]",
                    text,
                )

            if match:
                symbols.append({
                    "import": match.group(1),
                    "line": line_number(node),
                })

    return symbols


# ============================================================
# TREE WALK
# ============================================================

def walk_tree(root):
    stack = [root]

    while stack:
        node = stack.pop()

        yield node

        stack.extend(
            reversed(node.children)
        )


# ============================================================
# CALL EXTRACTION
# ============================================================

def extract_calls(
    root,
    source: bytes,
    file_path: str,
):
    calls = []

    for node in walk_tree(root):

        if node.type in (
            "call",
            "call_expression",
            "method_invocation",
        ):
            function_node = (
                node.child_by_field_name("function")
                or node.child_by_field_name("name")
            )

            if function_node:
                name = node_text(
                    function_node,
                    source,
                )

                calls.append({
                    "name": name,
                    "line": line_number(node),
                })

        elif node.type in (
            "new_expression",
            "object_creation_expression",
        ):
            type_node = (
                node.child_by_field_name("type")
            )

            if type_node:
                name = node_text(
                    type_node,
                    source,
                )

                calls.append({
                    "name": name,
                    "line": line_number(node),
                    "instantiation": True,
                })

    return calls


# ============================================================
# SYMBOL RESOLUTION
# ============================================================

def resolve_symbol(
    name: str,
    symbols_by_name: dict[str, list[str]],
):
    candidates = symbols_by_name.get(name, [])

    if len(candidates) == 1:
        return candidates[0]

    return None


# ============================================================
# IMPORT RESOLUTION
# ============================================================

def resolve_import(
    import_name: str,
    current_file: str,
    file_paths: set[str],
    language: str,
):
    import_name = import_name.strip()

    if not import_name:
        return None

    if language == "python":
        module_path = import_name.replace(
            ".",
            "/",
        )

        candidates = [
            f"{module_path}.py",
            f"{module_path}/__init__.py",
        ]

        for candidate in candidates:
            if candidate in file_paths:
                return candidate

    elif language in (
        "javascript",
        "typescript",
        "tsx",
    ):
        if not import_name.startswith("."):
            return None

        current_dir = os.path.dirname(
            current_file
        )

        candidate = normalize_path(
            os.path.normpath(
                os.path.join(
                    current_dir,
                    import_name,
                )
            )
        )

        candidates = [
            candidate,
            candidate + ".js",
            candidate + ".jsx",
            candidate + ".ts",
            candidate + ".tsx",
            candidate + "/index.js",
            candidate + "/index.jsx",
            candidate + "/index.ts",
            candidate + "/index.tsx",
        ]

        for item in candidates:
            item = normalize_path(item)

            if item in file_paths:
                return item

    elif language == "java":
        java_path = (
            import_name
            .replace(".", "/")
            + ".java"
        )

        if java_path in file_paths:
            return java_path

    return None


# ============================================================
# REPOSITORY ANALYZER
# ============================================================

class RepositoryAnalyzer:

    def __init__(
        self,
        github: GitHubClient,
        owner: str,
        repo: str,
        max_files: int,
    ):
        self.github = github
        self.owner = owner
        self.repo = repo
        self.max_files = max_files

        self.graph = Graph()

        self.file_symbols: dict[
            str,
            list[dict]
        ] = {}

        self.symbols_by_name: dict[
            str,
            list[str]
        ] = {}

        self.file_languages: dict[
            str,
            str
        ] = {}

        self.file_paths: set[str] = set()

    def analyze(self):

        # ----------------------------------------------------
        # Repository metadata
        # ----------------------------------------------------

        repository = self.github.get_repository(
            self.owner,
            self.repo,
        )

        default_branch = repository[
            "default_branch"
        ]

        # ----------------------------------------------------
        # Resolve branch SHA
        # ----------------------------------------------------

        branch_response = self.github.request(
            "GET",
            f"{GITHUB_API_URL}/repos/"
            f"{self.owner}/{self.repo}/branches/"
            f"{default_branch}",
        ).json()

        tree_sha = branch_response[
            "commit"
        ]["commit"]["tree"]["sha"]

        # ----------------------------------------------------
        # Repository node
        # ----------------------------------------------------

        repo_id = make_id(
            self.owner,
            self.repo,
        )

        self.graph.add_node(
            repo_id,
            "repository",
            self.repo,
            None,
            None,
            owner=self.owner,
            default_branch=default_branch,
        )

        # ----------------------------------------------------
        # Recursive tree
        # ----------------------------------------------------

        ast = self.github.get_tree(
            self.owner,
            self.repo,
            tree_sha,
        )

        entries = ast.get(
            "tree",
            [],
        )

        source_files = []

        for entry in entries:

            if entry.get("type") != "blob":
                continue

            path = normalize_path(
                entry.get("path", "")
            )

            if should_ignore(path):
                continue

            language = language_for_path(path)

            if not language:
                continue

            size = entry.get(
                "size",
                0,
            )

            if size and size > MAX_FILE_SIZE:
                continue

            source_files.append(
                (path, language, entry)
            )

        source_files = source_files[
            :self.max_files
        ]

        self.file_paths = {
            path
            for path, _, _ in source_files
        }

        # ----------------------------------------------------
        # Directory nodes
        # ----------------------------------------------------

        directories = set()

        for path, _, _ in source_files:

            parts = path.split("/")

            current = ""

            for part in parts[:-1]:

                current = (
                    f"{current}/{part}"
                    if current
                    else part
                )

                directories.add(current)

        for directory in sorted(
            directories,
            key=lambda x: x.count("/"),
        ):
            directory_id = make_id(
                repo_id,
                directory,
            )

            self.graph.add_node(
                directory_id,
                "directory",
                directory.split("/")[-1],
                None,
                None,
                path=directory,
            )

        # ----------------------------------------------------
        # File nodes
        # ----------------------------------------------------

        for path, language, entry in source_files:

            self.file_languages[path] = language

            file_id = make_id(
                repo_id,
                path,
            )

            self.graph.add_node(
                file_id,
                "file",
                path.split("/")[-1],
                path,
                None,
                language=language,
                size=entry.get("size", 0),
            )

            # Repository -> directory/file
            parts = path.split("/")

            if len(parts) == 1:
                self.graph.add_edge(
                    repo_id,
                    file_id,
                    "contains",
                )

            else:
                directory = "/".join(
                    parts[:-1]
                )

                directory_id = make_id(
                    repo_id,
                    directory,
                )

                self.graph.add_edge(
                    directory_id,
                    file_id,
                    "contains",
                )

                parent = parts[:-1]

                if len(parent) > 1:
                    for i in range(1, len(parent)):
                        parent_path = "/".join(
                            parent[:i]
                        )

                        parent_id = make_id(
                            repo_id,
                            parent_path,
                        )

                        child_path = "/".join(
                            parent[:i + 1]
                        )

                        child_id = make_id(
                            repo_id,
                            child_path,
                        )

                        self.graph.add_edge(
                            parent_id,
                            child_id,
                            "contains",
                        )

        # ----------------------------------------------------
        # Parse files
        # ----------------------------------------------------

        for path, language, entry in source_files:

            blob = self.github.get_blob(
                self.owner,
                self.repo,
                entry["sha"],
            )

            source_text = safe_decode_blob(
                blob
            )

            if not source_text:
                continue

            source_bytes = source_text.encode(
                "utf-8"
            )

            parser = create_parser(
                language
            )

            tree = parser.parse(
                source_bytes
            )

            root = tree.root_node

            file_id = make_id(
                repo_id,
                path,
            )

            if language == "python":
                symbols = extract_python(
                    root,
                    source_bytes,
                    path,
                    self.graph,
                )

            elif language == "java":
                symbols = extract_java(
                    root,
                    source_bytes,
                    path,
                    self.graph,
                )

            else:
                symbols = extract_javascript(
                    root,
                    source_bytes,
                    path,
                    self.graph,
                )

            self.file_symbols[path] = symbols

            # ------------------------------------------------
            # Register symbols globally
            # ------------------------------------------------

            for symbol in symbols:

                if "id" not in symbol:
                    continue

                name = symbol["name"]

                self.symbols_by_name.setdefault(
                    name,
                    [],
                ).append(
                    symbol["id"]
                )

                self.graph.add_edge(
                    file_id,
                    symbol["id"],
                    "contains",
                )

            # ------------------------------------------------
            # Imports
            # ------------------------------------------------

            for symbol in symbols:

                if "import" not in symbol:
                    continue

                imported_file = resolve_import(
                    symbol["import"],
                    path,
                    self.file_paths,
                    language,
                )

                if imported_file:

                    imported_file_id = make_id(
                        repo_id,
                        imported_file,
                    )

                    self.graph.add_edge(
                        file_id,
                        imported_file_id,
                        "imports",
                        symbol.get("line"),
                    )

            # ------------------------------------------------
            # Calls
            # ------------------------------------------------

            calls = extract_calls(
                root,
                source_bytes,
                path,
            )

            for call in calls:

                target = resolve_symbol(
                    call["name"],
                    self.symbols_by_name,
                )

                if target:

                    caller = self.find_containing_symbol(
                        path,
                        call["line"],
                    )

                    if caller:

                        edge_type = (
                            "instantiates"
                            if call.get(
                                "instantiation"
                            )
                            else "calls"
                        )

                        self.graph.add_edge(
                            caller,
                            target,
                            edge_type,
                            call["line"],
                        )

        # ----------------------------------------------------
        # Dependency edges
        # ----------------------------------------------------

        self.build_dependency_edges(
            repo_id
        )

        # ----------------------------------------------------
        # Statistics
        # ----------------------------------------------------

        node_types = {}

        for node in self.graph.nodes.values():

            node_type = node["type"]

            node_types[node_type] = (
                node_types.get(
                    node_type,
                    0,
                ) + 1
            )

        edge_types = {}

        for edge in self.graph.edges.values():

            edge_type = edge["type"]

            edge_types[edge_type] = (
                edge_types.get(
                    edge_type,
                    0,
                ) + 1
            )

        return {
            "repository": {
                "owner": self.owner,
                "name": self.repo,
                "url": repository["html_url"],
                "default_branch": default_branch,
            },
            "graph": self.graph.json(),
            "stats": {
                "files": len(
                    self.file_paths
                ),
                "nodes": len(
                    self.graph.nodes
                ),
                "edges": len(
                    self.graph.edges
                ),
                "node_types": node_types,
                "edge_types": edge_types,
            },
            "tree_truncated": ast.get(
                "truncated",
                False,
            ),
        }

    def find_containing_symbol(
        self,
        file_path: str,
        line: int,
    ):
        symbols = self.file_symbols.get(
            file_path,
            [],
        )

        candidates = [
            symbol
            for symbol in symbols
            if symbol.get("id")
            and symbol.get("line", 0) <= line
        ]

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x["line"],
            reverse=True,
        )

        return candidates[0]["id"]

    def build_dependency_edges(
        self,
        repo_id: str,
    ):
        for edge in list(
            self.graph.edges.values()
        ):

            source_node = self.graph.nodes.get(
                edge["source"]
            )

            target_node = self.graph.nodes.get(
                edge["target"]
            )

            if not source_node or not target_node:
                continue

            source_file = source_node.get(
                "file"
            )

            target_file = target_node.get(
                "file"
            )

            if (
                not source_file
                or not target_file
                or source_file == target_file
            ):
                continue

            if edge["type"] not in {
                "imports",
                "calls",
                "instantiates",
                "inherits",
                "implements",
            }:
                continue

            source_file_id = make_id(
                repo_id,
                source_file,
            )

            target_file_id = make_id(
                repo_id,
                target_file,
            )

            self.graph.add_edge(
                source_file_id,
                target_file_id,
                "depends",
                edge.get("line"),
            )


# ============================================================
# AUTH HELPERS
# ============================================================

def get_session_id(
    request: Request,
):
    return request.cookies.get(
        "codeatlas_session"
    )


def get_session(
    request: Request,
):
    session_id = get_session_id(
        request
    )

    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated with GitHub.",
        )

    session = SESSIONS.get(
        session_id
    )

    if not session:
        raise HTTPException(
            status_code=401,
            detail="Authentication session expired.",
        )

    return session


# ============================================================
# AUTH ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "name": "CodeAtlas API",
        "status": "running",
    }


@app.get("/auth/github/login")
def github_login():

    if not GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GitHub OAuth is not configured.",
        )

    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": (
            f"{BACKEND_URL}/auth/github/callback"
        ),
        "scope": "repo read:user user:email",
    }

    query = "&".join(
        f"{key}={requests.utils.quote(str(value))}"
        for key, value in params.items()
    )

    return RedirectResponse(
        f"{GITHUB_AUTHORIZE_URL}?{query}"
    )


@app.get("/auth/github/callback")
def github_callback(
    code: str,
):

    response = requests.post(
        GITHUB_ACCESS_TOKEN_URL,
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": (
                f"{BACKEND_URL}/auth/github/callback"
            ),
        },
        headers={
            "Accept": "application/json",
            "User-Agent": "CodeAtlas/1.0",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth token exchange failed.",
        )

    data = response.json()

    access_token = data.get(
        "access_token"
    )

    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="GitHub did not return an access token.",
        )

    # --------------------------------------------------------
    # Get authenticated GitHub user
    # --------------------------------------------------------

    github_response = requests.get(
        f"{GITHUB_API_URL}/user",
        headers={
            "Authorization": (
                f"Bearer {access_token}"
            ),
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeAtlas/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )

    if github_response.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve GitHub user.",
        )

    github_user = github_response.json()

    # --------------------------------------------------------
    # Create server-side session
    # --------------------------------------------------------

    session_id = uuid.uuid4().hex

    SESSIONS[session_id] = {
        "github_access_token": access_token,
        "github_user": {
            "id": github_user.get("id"),
            "login": github_user.get("login"),
            "name": github_user.get("name"),
            "avatar_url": github_user.get(
                "avatar_url"
            ),
            "html_url": github_user.get(
                "html_url"
            ),
        },
    }

    response = RedirectResponse(
        FRONTEND_URL
    )

    response.set_cookie(
        key="codeatlas_session",
        value=session_id,
        httponly=True,
        secure=False,       # True in HTTPS production
        samesite="lax",
        max_age=60 * 60 * 24,
        path="/",
    )

    return response


@app.get("/auth/me")
def get_current_user(
    request: Request,
):
    session = get_session(
        request
    )

    return {
        "authenticated": True,
        "user": session["github_user"],
    }


@app.post("/auth/logout")
def logout(
    request: Request,
):

    session_id = get_session_id(
        request
    )

    if session_id:
        SESSIONS.pop(
            session_id,
            None,
        )

    response = {
        "authenticated": False,
    }

    return response


# ============================================================
# ANALYZE REQUEST
# ============================================================

class AnalyzeRequest(BaseModel):
    repo_url: str
    max_files: int = DEFAULT_MAX_FILES


# ============================================================
# ANALYZE
# ============================================================

@app.post("/api/analyze")
def analyze_repository(
    request: AnalyzeRequest,
    http_request: Request,
):

    session = get_session(
        http_request
    )

    if request.max_files < 1:
        raise HTTPException(
            status_code=400,
            detail="max_files must be greater than 0.",
        )

    github_token = session[
        "github_access_token"
    ]

    owner, repo = parse_github_url(
        request.repo_url
    )

    github = GitHubClient(
        github_token
    )

    analyzer = RepositoryAnalyzer(
        github=github,
        owner=owner,
        repo=repo,
        max_files=request.max_files,
    )

    # IMPORTANT:
    #
    # The graph is generated and immediately returned.
    #
    # It is NOT stored in SESSIONS.
    # It is NOT stored in an ANALYSES dictionary.
    #

    result = analyzer.analyze()

    return result


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )