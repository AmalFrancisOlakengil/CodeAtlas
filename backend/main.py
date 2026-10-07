from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import uuid
from collections import defaultdict
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tree_sitter import Language, Parser

import tree_sitter_python as ts_python
import tree_sitter_java as ts_java
import tree_sitter_javascript as ts_javascript
import tree_sitter_typescript as ts_typescript


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="CodeAtlas",
    description="Static code intelligence and master graph generator",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Constants
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

    # Python
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

    # Java
    ".gradle",
    ".gradle-cache",

    # JS / TS
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
    ".7z",
    ".rar",
    ".mp3",
    ".mp4",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}

MAX_FILE_SIZE = 1_000_000


# ============================================================
# In-memory analysis storage
# ============================================================

ANALYSES: dict[str, dict[str, Any]] = {}


# ============================================================
# Pydantic models
# ============================================================

class AnalyzeRequest(BaseModel):
    repo_url: str
    max_files: int = 500
    github_token: Optional[str] = None


# ============================================================
# Tree-sitter setup
# ============================================================

LANGUAGES = {
    "python": Language(ts_python.language()),
    "java": Language(ts_java.language()),
    "javascript": Language(ts_javascript.language()),
    "typescript": Language(ts_typescript.language_typescript()),
    "tsx": Language(ts_typescript.language_tsx()),
}


def create_parser(language_name: str) -> Parser:
    parser = Parser()
    parser.language = LANGUAGES[language_name]
    return parser


# ============================================================
# Utility functions
# ============================================================

def node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8",
        errors="replace",
    )


def line_number(node) -> int:
    return node.start_point[0] + 1


def make_id(*parts: str) -> str:
    return "::".join(parts)


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def should_ignore(path: str) -> bool:
    parts = normalize_path(path).split("/")

    for part in parts[:-1]:
        if part in IGNORED_DIRECTORIES:
            return True

    filename = parts[-1]

    for suffix in IGNORED_SUFFIXES:
        if filename.endswith(suffix):
            return True

    if filename.endswith(".min.js"):
        return True

    return False


def extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def language_for_path(path: str) -> Optional[str]:
    return SUPPORTED_EXTENSIONS.get(extension(path))


def safe_decode_blob(data: dict) -> str:
    content = data.get("content", "")
    encoding = data.get("encoding")

    if encoding == "base64":
        raw = base64.b64decode(content)
        return raw.decode("utf-8", errors="replace")

    return content


# ============================================================
# GitHub client
# ============================================================

class GitHubClient:

    def __init__(self, token: Optional[str] = None):
        self.session = requests.Session()

        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "CodeAtlas/1.0",
        })

        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def request(self, url: str, params=None) -> dict:
        response = self.session.get(
            url,
            params=params,
            timeout=30,
        )

        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text

            raise RuntimeError(
                f"GitHub API error {response.status_code}: {detail}"
            )

        return response.json()

    def get_repository(self, owner: str, repo: str):
        return self.request(
            f"https://api.github.com/repos/{owner}/{repo}"
        )

    def get_tree(self, owner: str, repo: str, sha: str):
        return self.request(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{sha}",
            params={"recursive": "1"},
        )

    def get_blob(self, owner: str, repo: str, sha: str):
        return self.request(
            f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}"
        )


# ============================================================
# GitHub URL parsing
# ============================================================

def parse_github_url(repo_url: str):
    repo_url = repo_url.strip().rstrip("/")

    if repo_url.endswith(".git"):
        repo_url = repo_url[:-4]

    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)$",
        repo_url,
    )

    if not match:
        raise ValueError(
            "Expected a GitHub repository URL such as "
            "https://github.com/owner/repository"
        )

    return match.group(1), match.group(2)


# ============================================================
# Graph
# ============================================================

class Graph:

    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []

        self.edge_keys = set()

    def add_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        file: Optional[str] = None,
        line: Optional[int] = None,
        language: Optional[str] = None,
    ):
        if node_id not in self.nodes:
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "name": name,
                "file": file,
                "line": line,
                "language": language,
            }

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        line: Optional[int] = None,
    ):
        if source == target:
            return

        key = (source, target, edge_type)

        if key in self.edge_keys:
            return

        self.edge_keys.add(key)

        self.edges.append({
            "source": source,
            "target": target,
            "type": edge_type,
            "line": line,
        })

    def adjacency(self):
        result = defaultdict(lambda: defaultdict(list))

        for edge in self.edges:
            result[edge["source"]][edge["type"]].append(
                edge["target"]
            )

        return {
            source: dict(edge_types)
            for source, edge_types in result.items()
        }


# ============================================================
# Repository Analyzer
# ============================================================

class RepositoryAnalyzer:

    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
        max_files: int = 500,
    ):
        self.owner = owner
        self.repo = repo
        self.max_files = max_files

        self.github = GitHubClient(token)

        self.graph = Graph()

        self.files: dict[str, dict[str, Any]] = {}

        # Symbol indexes
        self.symbols_by_name = defaultdict(list)
        self.symbols_by_file = defaultdict(list)

        # Import relationships
        self.imports_by_file = defaultdict(list)

        # Used later for dependency inference
        self.file_relationships = defaultdict(set)

    # --------------------------------------------------------
    # Main
    # --------------------------------------------------------

    def analyze(self):

        metadata = self.github.get_repository(
            self.owner,
            self.repo,
        )

        default_branch = metadata["default_branch"]

        branch_data = self.github.request(
            f"https://api.github.com/repos/"
            f"{self.owner}/{self.repo}/branches/{default_branch}"
        )

        tree_sha = branch_data["commit"]["sha"]

        tree = self.github.get_tree(
            self.owner,
            self.repo,
            tree_sha,
        )

        entries = tree.get("tree", [])

        source_entries = []

        for entry in entries:

            if entry.get("type") != "blob":
                continue

            path = normalize_path(entry.get("path", ""))

            if should_ignore(path):
                continue

            lang = language_for_path(path)

            if not lang:
                continue

            size = entry.get("size", 0)

            if size and size > MAX_FILE_SIZE:
                continue

            source_entries.append({
                "path": path,
                "sha": entry.get("sha"),
                "size": size,
                "language": lang,
            })

        source_entries = source_entries[:self.max_files]

        # Build repository hierarchy
        self.build_file_nodes(
            source_entries,
            metadata,
        )

        # Fetch and parse
        for entry in source_entries:
            self.process_file(entry)

        # Cross-file dependency edges
        self.build_dependency_edges()

        return {
            "repository": {
                "owner": self.owner,
                "name": self.repo,
                "full_name": metadata.get("full_name"),
                "default_branch": default_branch,
                "stars": metadata.get("stargazers_count", 0),
                "language": metadata.get("language"),
            },
            "nodes": list(self.graph.nodes.values()),
            "edges": self.graph.edges,
            "adjacency": self.graph.adjacency(),
            "stats": self.get_stats(),
            "tree_truncated": tree.get("truncated", False),
        }

    # --------------------------------------------------------
    # Repository hierarchy
    # --------------------------------------------------------

    def build_file_nodes(
        self,
        entries: list[dict],
        metadata: dict,
    ):

        repository_id = "repository"

        self.graph.add_node(
            repository_id,
            "repository",
            metadata.get("name", self.repo),
        )

        directories = set()

        for entry in entries:

            path = entry["path"]

            parts = path.split("/")

            current_parent = repository_id

            for index in range(len(parts) - 1):

                directory = parts[index]

                directory_path = "/".join(parts[:index + 1])

                directory_id = f"directory::{directory_path}"

                if directory_id not in directories:

                    self.graph.add_node(
                        directory_id,
                        "directory",
                        directory,
                    )

                    directories.add(directory_id)

                    self.graph.add_edge(
                        current_parent,
                        directory_id,
                        "contains",
                    )

                current_parent = directory_id

            file_id = f"file::{path}"

            self.graph.add_node(
                file_id,
                "file",
                os.path.basename(path),
                file=path,
                language=entry["language"],
            )

            self.graph.add_edge(
                current_parent,
                file_id,
                "contains",
            )

            self.files[path] = {
                **entry,
                "node_id": file_id,
            }

    # --------------------------------------------------------
    # File processing
    # --------------------------------------------------------

    def process_file(self, entry):

        path = entry["path"]
        sha = entry["sha"]

        try:
            blob = self.github.get_blob(
                self.owner,
                self.repo,
                sha,
            )

            source = safe_decode_blob(blob)

        except Exception as exc:
            print(f"Failed to fetch {path}: {exc}")
            return

        self.files[path]["source"] = source

        language = entry["language"]

        parser = create_parser(language)

        tree = parser.parse(
            source.encode("utf-8")
        )

        root = tree.root_node

        if root.has_error:
            # We still process the tree because Tree-sitter
            # can provide useful partial syntax trees.
            pass

        self.extract_file(
            path,
            language,
            source.encode("utf-8"),
            root,
        )

    # --------------------------------------------------------
    # Generic traversal
    # --------------------------------------------------------

    def walk(self, node):

        yield node

        for child in node.children:
            yield from self.walk(child)

    # --------------------------------------------------------
    # Add declaration
    # --------------------------------------------------------

    def register_symbol(
        self,
        path: str,
        name: str,
        node_type: str,
        node,
        parent_id: Optional[str] = None,
    ) -> str:

        file_id = self.files[path]["node_id"]

        if parent_id:
            symbol_id = make_id(
                path,
                parent_id.split("::")[-1],
                name,
            )
        else:
            symbol_id = make_id(path, name)

        # Make collision-safe
        original_id = symbol_id
        counter = 2

        while symbol_id in self.graph.nodes:

            existing = self.graph.nodes[symbol_id]

            if (
                existing.get("file") == path
                and existing.get("name") == name
            ):
                break

            symbol_id = f"{original_id}#{counter}"
            counter += 1

        self.graph.add_node(
            symbol_id,
            node_type,
            name,
            file=path,
            line=line_number(node),
            language=self.files[path]["language"],
        )

        self.graph.add_edge(
            parent_id or file_id,
            symbol_id,
            "contains",
            line_number(node),
        )

        self.symbols_by_name[name].append(symbol_id)
        self.symbols_by_file[path].append(symbol_id)

        return symbol_id

    # ========================================================
    # Language extraction
    # ========================================================

    def extract_file(
        self,
        path: str,
        language: str,
        source: bytes,
        root,
    ):

        if language == "python":
            self.extract_python(path, source, root)

        elif language == "java":
            self.extract_java(path, source, root)

        elif language in {
            "javascript",
            "typescript",
            "tsx",
        }:
            self.extract_javascript(
                path,
                source,
                root,
            )

    # ========================================================
    # Python
    # ========================================================

    def extract_python(
        self,
        path,
        source,
        root,
    ):

        symbol_stack = []

        for node in self.walk(root):

            if node.type in {
                "function_definition",
                "async_function_definition",
            }:

                name_node = node.child_by_field_name("name")

                if not name_node:
                    continue

                name = node_text(
                    name_node,
                    source,
                )

                parent_id = (
                    symbol_stack[-1]
                    if symbol_stack
                    else None
                )

                symbol_id = self.register_symbol(
                    path,
                    name,
                    "function",
                    node,
                    parent_id,
                )

                self.extract_python_calls(
                    path,
                    node,
                    source,
                    symbol_id,
                )

                self.extract_python_imports(
                    path,
                    node,
                    source,
                )

            elif node.type == "class_definition":

                name_node = node.child_by_field_name("name")

                if not name_node:
                    continue

                name = node_text(
                    name_node,
                    source,
                )

                class_id = self.register_symbol(
                    path,
                    name,
                    "class",
                    node,
                )

                # Base classes
                bases = node.child_by_field_name("superclasses")

                if bases:

                    for base in self.walk(bases):

                        if base.type in {
                            "identifier",
                            "attribute",
                        }:

                            base_name = node_text(
                                base,
                                source,
                            )

                            target = self.resolve_symbol(
                                base_name,
                                path,
                            )

                            if target:
                                self.graph.add_edge(
                                    class_id,
                                    target,
                                    "inherits",
                                    line_number(node),
                                )

        # Imports are easier to extract globally.
        self.extract_python_imports(
            path,
            root,
            source,
        )

        self.extract_generic_calls(
            path,
            root,
            source,
            language="python",
        )

    def extract_python_imports(
        self,
        path,
        node,
        source,
    ):

        for current in self.walk(node):

            if current.type == "import_statement":

                text = node_text(
                    current,
                    source,
                )

                # import foo
                # import foo.bar
                names = text.replace(
                    "import ",
                    "",
                    1,
                ).split(",")

                for item in names:

                    item = item.strip()

                    if " as " in item:
                        item = item.split(" as ")[0]

                    module_name = item.strip()

                    if module_name:
                        self.imports_by_file[path].append(
                            module_name
                        )

                        self.add_import_edge(
                            path,
                            module_name,
                            line_number(current),
                        )

            elif current.type == "import_from_statement":

                text = node_text(
                    current,
                    source,
                )

                match = re.match(
                    r"from\s+([^\s]+)\s+import\s+(.+)",
                    text,
                    re.DOTALL,
                )

                if not match:
                    continue

                module = match.group(1)

                self.imports_by_file[path].append(
                    module
                )

                self.add_import_edge(
                    path,
                    module,
                    line_number(current),
                )

    # ========================================================
    # Java
    # ========================================================

    def extract_java(
        self,
        path,
        source,
        root,
    ):

        class_stack = []

        for node in self.walk(root):

            if node.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
            }:

                name_node = node.child_by_field_name("name")

                if not name_node:
                    continue

                name = node_text(
                    name_node,
                    source,
                )

                if node.type == "interface_declaration":
                    node_type = "interface"
                else:
                    node_type = "class"

                class_id = self.register_symbol(
                    path,
                    name,
                    node_type,
                    node,
                )

                # extends / implements
                declaration = node_text(
                    node,
                    source,
                )

                extends_match = re.search(
                    r"\bextends\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)",
                    declaration,
                )

                if extends_match:

                    base = extends_match.group(1)

                    target = self.resolve_symbol(
                        base.split(".")[-1],
                        path,
                    )

                    if target:
                        self.graph.add_edge(
                            class_id,
                            target,
                            "inherits",
                            line_number(node),
                        )

                implements_match = re.search(
                    r"\bimplements\s+([^{]+)",
                    declaration,
                )

                if implements_match:

                    interfaces = implements_match.group(1)

                    for interface in interfaces.split(","):

                        interface = interface.strip()

                        interface = re.split(
                            r"[\s<]",
                            interface,
                        )[0]

                        target = self.resolve_symbol(
                            interface,
                            path,
                        )

                        if target:
                            self.graph.add_edge(
                                class_id,
                                target,
                                "implements",
                                line_number(node),
                            )

                # Methods
                for child in node.children:

                    if child.type in {
                        "method_declaration",
                        "constructor_declaration",
                    }:

                        method_name_node = (
                            child.child_by_field_name("name")
                        )

                        if not method_name_node:
                            continue

                        method_name = node_text(
                            method_name_node,
                            source,
                        )

                        method_id = self.register_symbol(
                            path,
                            method_name,
                            "method",
                            child,
                            class_id,
                        )

                        self.extract_generic_calls(
                            path,
                            child,
                            source,
                            language="java",
                            current_symbol=method_id,
                        )

        # Imports
        for node in self.walk(root):

            if node.type == "import_declaration":

                text = node_text(
                    node,
                    source,
                )

                text = text.replace(
                    "import",
                    "",
                    1,
                ).strip()

                text = text.rstrip(";").strip()

                if text.startswith("static "):
                    text = text[len("static "):].strip()

                self.imports_by_file[path].append(
                    text
                )

                self.add_import_edge(
                    path,
                    text,
                    line_number(node),
                )

    # ========================================================
    # JavaScript / TypeScript
    # ========================================================

    def extract_javascript(
        self,
        path,
        source,
        root,
    ):

        for node in self.walk(root):

            if node.type in {
                "function_declaration",
            }:

                name_node = node.child_by_field_name("name")

                if not name_node:
                    continue

                name = node_text(
                    name_node,
                    source,
                )

                symbol_id = self.register_symbol(
                    path,
                    name,
                    "function",
                    node,
                )

                self.extract_generic_calls(
                    path,
                    node,
                    source,
                    language="javascript",
                    current_symbol=symbol_id,
                )

            elif node.type == "class_declaration":

                name_node = node.child_by_field_name("name")

                if not name_node:
                    continue

                name = node_text(
                    name_node,
                    source,
                )

                class_id = self.register_symbol(
                    path,
                    name,
                    "class",
                    node,
                )

                declaration = node_text(
                    node,
                    source,
                )

                extends_match = re.search(
                    r"\bextends\s+([A-Za-z_$][\w$]*)",
                    declaration,
                )

                if extends_match:

                    base = extends_match.group(1)

                    target = self.resolve_symbol(
                        base,
                        path,
                    )

                    if target:
                        self.graph.add_edge(
                            class_id,
                            target,
                            "inherits",
                            line_number(node),
                        )

                # Class methods
                for child in self.walk(node):

                    if child.type in {
                        "method_definition",
                        "public_field_definition",
                        "field_definition",
                    }:

                        name_node = (
                            child.child_by_field_name("name")
                        )

                        if not name_node:
                            continue

                        method_name = node_text(
                            name_node,
                            source,
                        )

                        method_id = self.register_symbol(
                            path,
                            method_name,
                            "method",
                            child,
                            class_id,
                        )

                        self.extract_generic_calls(
                            path,
                            child,
                            source,
                            language="javascript",
                            current_symbol=method_id,
                        )

            elif node.type in {
                "lexical_declaration",
                "variable_declaration",
            }:

                self.extract_arrow_functions(
                    path,
                    node,
                    source,
                )

        self.extract_js_imports(
            path,
            root,
            source,
        )

        self.extract_generic_calls(
            path,
            root,
            source,
            language="javascript",
        )

    def extract_arrow_functions(
        self,
        path,
        node,
        source,
    ):

        for child in self.walk(node):

            if child.type not in {
                "arrow_function",
                "function",
            }:
                continue

            parent = child.parent

            if not parent:
                continue

            # Look for:
            #
            # const foo = () => {}
            #
            # const foo = function() {}

            declaration = parent.parent

            if not declaration:
                continue

            text = node_text(
                declaration,
                source,
            )

            match = re.search(
                r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
                text,
            )

            if not match:
                continue

            name = match.group(1)

            symbol_id = self.register_symbol(
                path,
                name,
                "function",
                child,
            )

            self.extract_generic_calls(
                path,
                child,
                source,
                language="javascript",
                current_symbol=symbol_id,
            )

    def extract_js_imports(
        self,
        path,
        root,
        source,
    ):

        for node in self.walk(root):

            if node.type not in {
                "import_statement",
            }:
                continue

            text = node_text(
                node,
                source,
            )

            match = re.search(
                r"""from\s+["']([^"']+)["']""",
                text,
            )

            if not match:
                match = re.search(
                    r"""import\s+["']([^"']+)["']""",
                    text,
                )

            if not match:
                continue

            module = match.group(1)

            self.imports_by_file[path].append(
                module
            )

            self.add_import_edge(
                path,
                module,
                line_number(node),
            )

    # ========================================================
    # Generic call extraction
    # ========================================================

    def extract_generic_calls(
        self,
        path,
        node,
        source,
        language,
        current_symbol=None,
    ):

        for current in self.walk(node):

            if current.type not in {
                "call",
                "call_expression",
                "method_invocation",
                "new_expression",
                "object_creation_expression",
            }:
                continue

            function_node = (
                current.child_by_field_name("function")
                or current.child_by_field_name("name")
                or current.child_by_field_name("constructor")
            )

            if not function_node:
                continue

            name = node_text(
                function_node,
                source,
            )

            # Clean member calls:
            #
            # user.save()
            # service.getUser()
            #
            # -> getUser
            if "." in name:
                name = name.split(".")[-1]

            name = name.strip()

            if not name:
                continue

            target = self.resolve_symbol(
                name,
                path,
            )

            if not target:
                continue

            source_symbol = current_symbol

            if not source_symbol:
                source_symbol = self.find_containing_symbol(
                    path,
                    current,
                )

            if not source_symbol:
                source_symbol = self.files[path]["node_id"]

            edge_type = (
                "instantiates"
                if current.type in {
                    "new_expression",
                    "object_creation_expression",
                }
                else "calls"
            )

            self.graph.add_edge(
                source_symbol,
                target,
                edge_type,
                line_number(current),
            )

            self.file_relationships[path].add(
                self.graph.nodes[target].get("file")
            )

    # ========================================================
    # Python calls
    # ========================================================

    def extract_python_calls(
        self,
        path,
        node,
        source,
        current_symbol,
    ):

        self.extract_generic_calls(
            path,
            node,
            source,
            language="python",
            current_symbol=current_symbol,
        )

    # ========================================================
    # Find containing symbol
    # ========================================================

    def find_containing_symbol(
        self,
        path,
        node,
    ):

        best = None
        best_span = None

        for symbol_id in self.symbols_by_file[path]:

            graph_node = self.graph.nodes[symbol_id]

            line = graph_node.get("line")

            if line is None:
                continue

            # Approximate containment based on source position.
            # The exact Tree-sitter node isn't retained deliberately.
            if line <= line_number(node):

                if best_span is None or line > best_span:
                    best = symbol_id
                    best_span = line

        return best

    # ========================================================
    # Symbol resolution
    # ========================================================

    def resolve_symbol(
        self,
        name: str,
        current_file: str,
    ) -> Optional[str]:

        if not name:
            return None

        name = name.strip()

        candidates = []

        # Direct name
        candidates.extend(
            self.symbols_by_name.get(name, [])
        )

        # Java fully-qualified names
        short_name = name.split(".")[-1]

        if short_name != name:
            candidates.extend(
                self.symbols_by_name.get(short_name, [])
            )

        # Remove duplicates
        candidates = list(dict.fromkeys(candidates))

        if not candidates:
            return None

        # Same file gets priority
        same_file = [
            candidate
            for candidate in candidates
            if self.graph.nodes[candidate].get("file")
            == current_file
        ]

        if len(same_file) == 1:
            return same_file[0]

        # If exactly one global candidate exists,
        # resolution is safe enough for v1.
        if len(candidates) == 1:
            return candidates[0]

        return None

    # ========================================================
    # Import edges
    # ========================================================

    def add_import_edge(
        self,
        path,
        module,
        line,
    ):

        target_file = self.resolve_import_to_file(
            path,
            module,
        )

        if target_file:

            self.graph.add_edge(
                self.files[path]["node_id"],
                self.files[target_file]["node_id"],
                "imports",
                line,
            )

            self.file_relationships[path].add(
                target_file
            )

    def resolve_import_to_file(
        self,
        current_file,
        module,
    ):

        module = module.strip()

        if not module:
            return None

        # ----------------------------------------------------
        # Python
        # ----------------------------------------------------

        if self.files[current_file]["language"] == "python":

            module_path = module.replace(".", "/")

            candidates = [
                f"{module_path}.py",
                f"{module_path}/__init__.py",
            ]

            current_dir = os.path.dirname(current_file)

            if module.startswith("."):

                dots = len(module) - len(module.lstrip("."))

                module_name = module.lstrip(".").replace(
                    ".",
                    "/",
                )

                base = current_dir

                for _ in range(max(0, dots - 1)):
                    base = os.path.dirname(base)

                candidates = [
                    os.path.join(
                        base,
                        module_name + ".py",
                    ),
                    os.path.join(
                        base,
                        module_name,
                        "__init__.py",
                    ),
                ]

            for candidate in candidates:

                candidate = normalize_path(candidate)

                if candidate in self.files:
                    return candidate

        # ----------------------------------------------------
        # JS / TS
        # ----------------------------------------------------

        if module.startswith("."):

            base_dir = os.path.dirname(
                current_file
            )

            raw = normalize_path(
                os.path.join(
                    base_dir,
                    module,
                )
            )

            candidates = [
                raw,
                raw + ".js",
                raw + ".jsx",
                raw + ".ts",
                raw + ".tsx",
                raw + "/index.js",
                raw + "/index.jsx",
                raw + "/index.ts",
                raw + "/index.tsx",
            ]

            for candidate in candidates:

                candidate = normalize_path(candidate)

                if candidate in self.files:
                    return candidate

        # ----------------------------------------------------
        # Java
        # ----------------------------------------------------

        if (
            self.files[current_file]["language"]
            == "java"
        ):

            java_path = module.replace(
                ".",
                "/",
            ) + ".java"

            if java_path in self.files:
                return java_path

            class_name = module.split(".")[-1]

            for file_path in self.files:

                if (
                    os.path.basename(file_path)
                    == f"{class_name}.java"
                ):
                    return file_path

        return None

    # ========================================================
    # Dependency edges
    # ========================================================

    def build_dependency_edges(self):

        for source_file, targets in (
            self.file_relationships.items()
        ):

            source_id = self.files[source_file]["node_id"]

            for target_file in targets:

                if not target_file:
                    continue

                if target_file not in self.files:
                    continue

                target_id = self.files[target_file]["node_id"]

                self.graph.add_edge(
                    source_id,
                    target_id,
                    "depends",
                )

    # ========================================================
    # Stats
    # ========================================================

    def get_stats(self):

        nodes = list(
            self.graph.nodes.values()
        )

        edges = self.graph.edges

        node_types = defaultdict(int)

        edge_types = defaultdict(int)

        for node in nodes:
            node_types[node["type"]] += 1

        for edge in edges:
            edge_types[edge["type"]] += 1

        return {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
            "files_analyzed": len(self.files),
            "symbols": sum(
                1
                for node in nodes
                if node["type"] in {
                    "class",
                    "interface",
                    "function",
                    "method",
                }
            ),
        }


# ============================================================
# API endpoints
# ============================================================

@app.get("/")
def root():
    return {
        "name": "CodeAtlas",
        "version": "1.0.0",
        "status": "running",
    }


@app.post("/api/analyze")
def analyze_repository(request: AnalyzeRequest):

    try:
        owner, repo = parse_github_url(
            request.repo_url
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    analysis_id = str(uuid.uuid4())

    try:

        analyzer = RepositoryAnalyzer(
            owner=owner,
            repo=repo,
            token=request.github_token,
            max_files=min(
                max(request.max_files, 1),
                5000,
            ),
        )

        result = analyzer.analyze()

        ANALYSES[analysis_id] = result

        return {
            "analysis_id": analysis_id,
            "status": "completed",
            "repository": result["repository"],
            "stats": result["stats"],
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/api/analysis/{analysis_id}")
def get_analysis(analysis_id: str):

    result = ANALYSES.get(analysis_id)

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    return {
        "analysis_id": analysis_id,
        "status": "completed",
        "repository": result["repository"],
        "stats": result["stats"],
        "tree_truncated": result["tree_truncated"],
    }


@app.get("/api/analysis/{analysis_id}/graph")
def get_graph(analysis_id: str):

    result = ANALYSES.get(analysis_id)

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    return {
        "repository": result["repository"],
        "nodes": result["nodes"],
        "edges": result["edges"],
        "adjacency": result["adjacency"],
        "stats": result["stats"],
    }


# ============================================================
# Local execution
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )