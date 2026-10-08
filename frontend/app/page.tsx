"use client";

import dynamic from "next/dynamic";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { ForceGraphMethods } from "react-force-graph-2d";


const ForceGraph2D = dynamic(
  () => import("react-force-graph-2d"),
  {
    ssr: false,
  }
);


// ============================================================
// ENV
// ============================================================

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";


// ============================================================
// TYPES
// ============================================================

type NodeType =
  | "repository"
  | "directory"
  | "file"
  | "class"
  | "interface"
  | "function"
  | "method";

type EdgeType =
  | "contains"
  | "imports"
  | "calls"
  | "inherits"
  | "implements"
  | "instantiates"
  | "depends";

interface GraphNode {
  id: string;
  type: NodeType;
  name: string;
  file?: string | null;
  line?: number | null;
  language?: string;
  path?: string;
  owner?: string;
  default_branch?: string;
  size?: number;
}

interface GraphEdge {
  source: string | GraphNode;
  target: string | GraphNode;
  type: EdgeType;
  line?: number | null;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface RepositoryInfo {
  owner: string;
  name: string;
  url: string;
  default_branch: string;
}

interface AnalysisStats {
  files: number;
  nodes: number;
  edges: number;
  node_types: Record<string, number>;
  edge_types: Record<string, number>;
}

interface AnalysisResponse {
  repository: RepositoryInfo;
  graph: GraphData;
  stats: AnalysisStats;
  tree_truncated: boolean;
}

interface GithubUser {
  id: number;
  login: string;
  name?: string | null;
  avatar_url?: string;
  html_url?: string;
}

interface AuthResponse {
  authenticated: boolean;
  user?: GithubUser;
}


// ============================================================
// COLORS
// ============================================================

const NODE_COLORS: Record<NodeType, string> = {
  repository: "#f97316",
  directory: "#a855f7",
  file: "#64748b",
  class: "#3b82f6",
  interface: "#06b6d4",
  function: "#22c55e",
  method: "#eab308",
};

const EDGE_COLORS: Record<EdgeType, string> = {
  contains: "#64748b",
  imports: "#8b5cf6",
  calls: "#22c55e",
  inherits: "#3b82f6",
  implements: "#06b6d4",
  instantiates: "#eab308",
  depends: "#f97316",
};


// ============================================================
// COMPONENT
// ============================================================

export default function Home() {

  // ----------------------------------------------------------
  // AUTH
  // ----------------------------------------------------------

  const [user, setUser] =
    useState<GithubUser | null>(null);

  const [authLoading, setAuthLoading] =
    useState(true);

  // ----------------------------------------------------------
  // ANALYSIS
  // ----------------------------------------------------------

  const [repoUrl, setRepoUrl] =
    useState("");

  const [maxFiles, setMaxFiles] =
    useState(500);

  const [graph, setGraph] =
    useState<GraphData | null>(null);

  const [repository, setRepository] =
    useState<RepositoryInfo | null>(null);

  const [stats, setStats] =
    useState<AnalysisStats | null>(null);

  const [treeTruncated, setTreeTruncated] =
    useState(false);

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState("");

  const [selectedNode, setSelectedNode] =
    useState<GraphNode | null>(null);

  // ----------------------------------------------------------
  // FILTERS
  // ----------------------------------------------------------

  const [search, setSearch] =
    useState("");

  const [visibleNodeTypes, setVisibleNodeTypes] =
    useState<Set<NodeType>>(
      new Set([
        "repository",
        "directory",
        "file",
        "class",
        "interface",
        "function",
        "method",
      ])
    );

  const [visibleEdgeTypes, setVisibleEdgeTypes] =
    useState<Set<EdgeType>>(
      new Set([
        "contains",
        "imports",
        "calls",
        "inherits",
        "implements",
        "instantiates",
        "depends",
      ])
    );


  // ==========================================================
  // AUTHENTICATION
  // ==========================================================

  const checkAuthentication =
    useCallback(async () => {

      try {

        const response = await fetch(
          `${API_URL}/auth/me`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          setUser(null);
          return;
        }

        const data: AuthResponse =
          await response.json();

        if (data.authenticated && data.user) {
          setUser(data.user);
        } else {
          setUser(null);
        }

      } catch {
        setUser(null);
      } finally {
        setAuthLoading(false);
      }

    }, []);


  useEffect(() => {
    checkAuthentication();
  }, [checkAuthentication]);


  const loginWithGithub = () => {

    window.location.href =
      `${API_URL}/auth/github/login`;

  };


  const logout = async () => {

    try {

      await fetch(
        `${API_URL}/auth/logout`,
        {
          method: "POST",
          credentials: "include",
        }
      );

    } finally {

      setUser(null);

      // Graph is client-side state.
      setGraph(null);
      setRepository(null);
      setStats(null);
      setSelectedNode(null);
    }
  };


  // ==========================================================
  // ANALYZE REPOSITORY
  // ==========================================================

  const analyzeRepository =
    useCallback(async () => {

      if (!repoUrl.trim()) {
        setError(
          "Enter a GitHub repository URL."
        );
        return;
      }

      if (!user) {
        setError(
          "Please sign in with GitHub first."
        );
        return;
      }

      setLoading(true);
      setError("");
      setSelectedNode(null);

      try {

        const response = await fetch(
          `${API_URL}/api/analyze`,
          {
            method: "POST",

            headers: {
              "Content-Type":
                "application/json",
            },

            credentials: "include",

            body: JSON.stringify({
              repo_url: repoUrl,
              max_files: maxFiles,
            }),
          }
        );

        const data =
          await response.json();

        if (!response.ok) {

          throw new Error(
            data?.detail
              ? typeof data.detail ===
                "string"
                ? data.detail
                : JSON.stringify(
                    data.detail
                  )
              : "Analysis failed."
          );
        }

        const result =
          data as AnalysisResponse;

        // ----------------------------------------------------
        // IMPORTANT:
        //
        // The graph lives ONLY in React/browser memory.
        //
        // Backend does not retain it.
        // ----------------------------------------------------

        setGraph(result.graph);
        setRepository(
          result.repository
        );
        setStats(result.stats);
        setTreeTruncated(
          result.tree_truncated
        );

      } catch (err) {

        setError(
          err instanceof Error
            ? err.message
            : "Analysis failed."
        );

      } finally {

        setLoading(false);

      }

    }, [
      repoUrl,
      maxFiles,
      user,
    ]);


  // ==========================================================
  // KEYBOARD SHORTCUT
  // ==========================================================

  const handleKeyDown =
    (event: React.KeyboardEvent<HTMLInputElement>) => {

      if (
        event.key === "Enter" &&
        event.ctrlKey
      ) {
        analyzeRepository();
      }

    };


  // ==========================================================
  // FILTER GRAPH
  // ==========================================================

  const filteredGraph =
    useMemo(() => {

      if (!graph) {
        return {
          nodes: [],
          edges: [],
        };
      }

      const query =
        search
          .trim()
          .toLowerCase();

      const nodes =
        graph.nodes.filter(
          (node) => {

            if (
              !visibleNodeTypes.has(
                node.type
              )
            ) {
              return false;
            }

            if (!query) {
              return true;
            }

            return (
              node.name
                .toLowerCase()
                .includes(query) ||
              node.file
                ?.toLowerCase()
                .includes(query) ||
              node.path
                ?.toLowerCase()
                .includes(query)
            );
          }
        );

      const nodeIds =
        new Set(
          nodes.map(
            (node) => node.id
          )
        );

      const edges =
        graph.edges.filter(
          (edge) => {

            if (
              !visibleEdgeTypes.has(
                edge.type
              )
            ) {
              return false;
            }

            const sourceId =
              typeof edge.source ===
              "string"
                ? edge.source
                : edge.source.id;

            const targetId =
              typeof edge.target ===
              "string"
                ? edge.target
                : edge.target.id;

            return (
              nodeIds.has(sourceId) &&
              nodeIds.has(targetId)
            );
          }
        );

      return {
        nodes,
        edges,
      };

    }, [
      graph,
      search,
      visibleNodeTypes,
      visibleEdgeTypes,
    ]);


  // ==========================================================
  // TOGGLE HELPERS
  // ==========================================================

  const toggleNodeType =
    (type: NodeType) => {

      setVisibleNodeTypes(
        (previous) => {

          const next =
            new Set(previous);

          if (next.has(type)) {
            next.delete(type);
          } else {
            next.add(type);
          }

          return next;
        }
      );
    };


  const toggleEdgeType =
    (type: EdgeType) => {

      setVisibleEdgeTypes(
        (previous) => {

          const next =
            new Set(previous);

          if (next.has(type)) {
            next.delete(type);
          } else {
            next.add(type);
          }

          return next;
        }
      );
    };


  // ==========================================================
  // LOADING
  // ==========================================================

  if (authLoading) {

    return (
      <main className="min-h-screen bg-zinc-950 text-white flex items-center justify-center">
        <div className="text-zinc-400">
          Checking GitHub authentication...
        </div>
      </main>
    );

  }


  // ==========================================================
  // AUTH SCREEN
  // ==========================================================

  if (!user) {

    return (
      <main className="min-h-screen bg-zinc-950 text-white flex items-center justify-center px-6">

        <div className="w-full max-w-md">

          <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-8 shadow-2xl">

            <div className="mb-8">

              <h1 className="text-3xl font-bold tracking-tight">
                CodeAtlas
              </h1>

              <p className="mt-2 text-sm text-zinc-400">
                Static code intelligence for
                GitHub repositories.
              </p>

            </div>

            <button
              onClick={loginWithGithub}
              className="w-full rounded-xl bg-white px-5 py-3 font-semibold text-black transition hover:bg-zinc-200"
            >
              Continue with GitHub
            </button>

            <p className="mt-5 text-xs leading-5 text-zinc-500">
              CodeAtlas uses your GitHub
              authorization to access repositories
              through the GitHub API.
            </p>

          </div>

        </div>

      </main>
    );

  }


  // ==========================================================
  // MAIN UI
  // ==========================================================

  return (
    <main className="min-h-screen bg-zinc-950 text-white">

      {/* HEADER */}

      <header className="border-b border-zinc-800">

        <div className="mx-auto flex max-w-[1800px] items-center justify-between px-6 py-4">

          <div>

            <h1 className="text-xl font-bold">
              CodeAtlas
            </h1>

            <p className="text-xs text-zinc-500">
              Static code intelligence
            </p>

          </div>

          <div className="flex items-center gap-4">

            <div className="flex items-center gap-2">

              {user.avatar_url && (
                <img
                  src={user.avatar_url}
                  alt={user.login}
                  className="h-8 w-8 rounded-full"
                />
              )}

              <span className="text-sm text-zinc-300">
                @{user.login}
              </span>

            </div>

            <button
              onClick={logout}
              className="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800"
            >
              Logout
            </button>

          </div>

        </div>

      </header>


      {/* ANALYSIS BAR */}

      <section className="border-b border-zinc-800">

        <div className="mx-auto max-w-[1800px] px-6 py-5">

          <div className="flex gap-3">

            <input
              value={repoUrl}
              onChange={(event) =>
                setRepoUrl(
                  event.target.value
                )
              }
              onKeyDown={handleKeyDown}
              placeholder="https://github.com/owner/repository"
              className="min-w-0 flex-1 rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm outline-none placeholder:text-zinc-600 focus:border-zinc-500"
            />

            <input
              type="number"
              min={1}
              value={maxFiles}
              onChange={(event) =>
                setMaxFiles(
                  Number(event.target.value)
                )
              }
              className="w-28 rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm outline-none focus:border-zinc-500"
              title="Maximum source files"
            />

            <button
              onClick={analyzeRepository}
              disabled={loading}
              className="rounded-xl bg-white px-6 py-3 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading
                ? "Analyzing..."
                : "Analyze"}
            </button>

          </div>

          {error && (
            <div className="mt-3 rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
              {error}
            </div>
          )}

          {treeTruncated && (
            <div className="mt-3 rounded-lg border border-yellow-900 bg-yellow-950/30 px-4 py-3 text-sm text-yellow-300">
              GitHub reported a truncated
              repository tree. The displayed
              graph may not contain every file.
            </div>
          )}

        </div>

      </section>


      {/* STATS */}

      {stats && (

        <section className="border-b border-zinc-800">

          <div className="mx-auto grid max-w-[1800px] grid-cols-2 gap-px bg-zinc-800 md:grid-cols-4">

            <Stat
              label="Files"
              value={stats.files}
            />

            <Stat
              label="Nodes"
              value={stats.nodes}
            />

            <Stat
              label="Edges"
              value={stats.edges}
            />

            <Stat
              label="Branch"
              value={
                repository?.default_branch ||
                "-"
              }
            />

          </div>

        </section>

      )}


      {/* GRAPH */}

      <section className="mx-auto flex max-w-[1800px]">

        {/* LEFT FILTER PANEL */}

        <aside className="hidden w-64 shrink-0 border-r border-zinc-800 p-5 lg:block">

          <h2 className="mb-4 text-sm font-semibold">
            Filters
          </h2>

          <input
            value={search}
            onChange={(event) =>
              setSearch(
                event.target.value
              )
            }
            placeholder="Search nodes..."
            className="mb-5 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none"
          />

          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Nodes
          </h3>

          <div className="space-y-1">

            {(
              [
                "repository",
                "directory",
                "file",
                "class",
                "interface",
                "function",
                "method",
              ] as NodeType[]
            ).map((type) => (

              <FilterRow
                key={type}
                label={type}
                active={
                  visibleNodeTypes.has(
                    type
                  )
                }
                color={
                  NODE_COLORS[type]
                }
                onClick={() =>
                  toggleNodeType(type)
                }
              />

            ))}

          </div>

          <h3 className="mb-2 mt-7 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Edges
          </h3>

          <div className="space-y-1">

            {(
              [
                "contains",
                "imports",
                "calls",
                "inherits",
                "implements",
                "instantiates",
                "depends",
              ] as EdgeType[]
            ).map((type) => (

              <FilterRow
                key={type}
                label={type}
                active={
                  visibleEdgeTypes.has(
                    type
                  )
                }
                color={
                  EDGE_COLORS[type]
                }
                onClick={() =>
                  toggleEdgeType(type)
                }
              />

            ))}

          </div>

        </aside>


        {/* GRAPH CANVAS */}

        <div className="relative h-[calc(100vh-190px)] min-h-[600px] flex-1 overflow-hidden bg-zinc-950">

          {!graph && !loading && (

            <div className="absolute inset-0 flex items-center justify-center">

              <div className="text-center">

                <div className="mb-3 text-4xl">
                  ◎
                </div>

                <h2 className="text-lg font-semibold">
                  No repository analyzed
                </h2>

                <p className="mt-2 text-sm text-zinc-500">
                  Enter a GitHub repository above
                  to build its master graph.
                </p>

              </div>

            </div>

          )}

          {loading && (

            <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950/70 backdrop-blur-sm">

              <div className="rounded-xl border border-zinc-800 bg-zinc-900 px-6 py-5 text-center">

                <div className="mb-2 font-medium">
                  Building master graph
                </div>

                <div className="text-sm text-zinc-500">
                  Fetching and parsing repository
                  source...
                </div>

              </div>

            </div>

          )}

          {graph && (

            <ForceGraph2D
              graphData={{
    nodes: filteredGraph.nodes,
    links: filteredGraph.edges, // ✅ Maps `edges` to `links` expected by react-force-graph-2d
  }}

              backgroundColor="#09090b"

              nodeLabel={(node) => {
                const item =
                  node as GraphNode;

                return `
                  <div style="
                    padding: 6px;
                    font-family: sans-serif;
                  ">
                    <strong>${escapeHtml(
                      item.name
                    )}</strong>
                    <br/>
                    <span style="opacity:0.7">
                      ${escapeHtml(
                        item.type
                      )}
                    </span>
                    ${
                      item.file
                        ? `<br/><span style="opacity:0.6">${escapeHtml(
                            item.file
                          )}</span>`
                        : ""
                    }
                  </div>
                `;
              }}

              nodeColor={(node) =>
                NODE_COLORS[
                  (node as GraphNode)
                    .type
                ] || "#ffffff"
              }

              nodeVal={(node) => {

                const item =
                  node as GraphNode;

                switch (item.type) {

                  case "repository":
                    return 12;

                  case "directory":
                    return 7;

                  case "file":
                    return 5;

                  default:
                    return 4;
                }

              }}

              linkColor={(link) =>
                EDGE_COLORS[
                  link.type as EdgeType
                ] || "#52525b"
              }

              linkDirectionalArrowLength={
                4
              }

              linkDirectionalArrowRelPos={
                1
              }

              linkWidth={(link) =>
                link.type === "depends"
                  ? 2
                  : 1
              }

              onNodeClick={(node) =>
                setSelectedNode(
                  node as GraphNode
                )
              }

              onBackgroundClick={() =>
                setSelectedNode(null)
              }

              nodeCanvasObject={(
                node,
                ctx,
                globalScale
              ) => {

                const item =
                  node as GraphNode;

                const label =
                  item.name;

                const fontSize =
                  Math.max(
                    8,
                    12 / globalScale
                  );

                const radius =
                  item.type ===
                  "repository"
                    ? 7
                    : 4;

                ctx.beginPath();

                ctx.arc(
                  node.x ?? 0,
                  node.y ?? 0,
                  radius,
                  0,
                  2 * Math.PI
                );

                ctx.fillStyle =
                  NODE_COLORS[
                    item.type
                  ];

                ctx.fill();

                if (
                  globalScale > 1.5
                ) {

                  ctx.font =
                    `${fontSize}px Sans-Serif`;

                  ctx.textAlign =
                    "center";

                  ctx.textBaseline =
                    "top";

                  ctx.fillStyle =
                    "#e4e4e7";

                  ctx.fillText(
                    label,
                    node.x ?? 0,
                    (node.y ?? 0) +
                      radius +
                      2
                  );

                }

              }}

              cooldownTicks={100}

              warmupTicks={100}

              enableNodeDrag

              enableZoomInteraction

              enablePanInteraction

              minZoom={0.2}

              maxZoom={8}

              width={
                typeof window !==
                "undefined"
                  ? window.innerWidth
                  : undefined
              }

              height={
                typeof window !==
                "undefined"
                  ? window.innerHeight -
                    190
                  : undefined
              }
            />

          )}

        </div>


        {/* INSPECTOR */}

        {selectedNode && (

          <aside className="hidden w-80 shrink-0 border-l border-zinc-800 bg-zinc-950 p-5 xl:block">

            <NodeInspector
              node={selectedNode}
              graph={graph}
            />

          </aside>

        )}

      </section>

    </main>
  );
}


// ============================================================
// STAT
// ============================================================

function Stat({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {

  return (
    <div className="bg-zinc-950 px-6 py-4">

      <div className="text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </div>

      <div className="mt-1 text-lg font-semibold">
        {value}
      </div>

    </div>
  );
}


// ============================================================
// FILTER ROW
// ============================================================

function FilterRow({
  label,
  active,
  color,
  onClick,
}: {
  label: string;
  active: boolean;
  color: string;
  onClick: () => void;
}) {

  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition ${
        active
          ? "bg-zinc-800 text-zinc-200"
          : "text-zinc-600"
      }`}
    >

      <span
        className="h-2.5 w-2.5 rounded-full"
        style={{
          backgroundColor:
            active
              ? color
              : "#3f3f46",
        }}
      />

      {label}

    </button>
  );
}


// ============================================================
// NODE INSPECTOR
// ============================================================

function NodeInspector({
  node,
  graph,
}: {
  node: GraphNode;
  graph: GraphData | null;
}) {

  if (!graph) {
    return null;
  }

  const connections =
    graph.edges.filter(
      (edge) => {

        const sourceId =
          typeof edge.source ===
          "string"
            ? edge.source
            : edge.source.id;

        const targetId =
          typeof edge.target ===
          "string"
            ? edge.target
            : edge.target.id;

        return (
          sourceId === node.id ||
          targetId === node.id
        );

      }
    );

  return (
    <div>

      <div className="mb-5">

        <div
          className="mb-2 inline-flex rounded-full px-2 py-1 text-xs"
          style={{
            backgroundColor:
              `${NODE_COLORS[node.type]}22`,
            color:
              NODE_COLORS[node.type],
          }}
        >
          {node.type}
        </div>

        <h2 className="break-words text-lg font-semibold">
          {node.name}
        </h2>

      </div>


      <div className="space-y-3 text-sm">

        {node.file && (
          <div>

            <div className="text-xs text-zinc-500">
              File
            </div>

            <div className="mt-1 break-all text-zinc-300">
              {node.file}
            </div>

          </div>
        )}

        {node.line && (
          <div>

            <div className="text-xs text-zinc-500">
              Line
            </div>

            <div className="mt-1 text-zinc-300">
              {node.line}
            </div>

          </div>
        )}

        {node.language && (
          <div>

            <div className="text-xs text-zinc-500">
              Language
            </div>

            <div className="mt-1 text-zinc-300">
              {node.language}
            </div>

          </div>
        )}

      </div>


      <div className="mt-7">

        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Connections ({connections.length})
        </h3>

        <ConnectionList
          node={node}
          graph={graph}
          edges={connections}
        />

      </div>

    </div>
  );
}


// ============================================================
// CONNECTION LIST
// ============================================================

function ConnectionList({
  node,
  graph,
  edges,
}: {
  node: GraphNode;
  graph: GraphData;
  edges: GraphEdge[];
}) {

  return (
    <div className="space-y-2">

      {edges.slice(0, 50).map(
        (edge, index) => {

          const sourceId =
            typeof edge.source ===
            "string"
              ? edge.source
              : edge.source.id;

          const targetId =
            typeof edge.target ===
            "string"
              ? edge.target
              : edge.target.id;

          const otherId =
            sourceId === node.id
              ? targetId
              : sourceId;

          const otherNode =
            graph.nodes.find(
              (item) =>
                item.id === otherId
            );

          if (!otherNode) {
            return null;
          }

          const outgoing =
            sourceId === node.id;

          return (
            <div
              key={`${edge.type}-${index}`}
              className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2"
            >

              <div className="text-[11px] uppercase tracking-wider text-zinc-500">
                {outgoing
                  ? "→"
                  : "←"}{" "}
                {edge.type}
              </div>

              <div className="mt-1 truncate text-sm text-zinc-300">
                {otherNode.name}
              </div>

            </div>
          );

        }
      )}

    </div>
  );
}


// ============================================================
// HTML ESCAPE
// ============================================================

function escapeHtml(
  value: string
): string {

  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}