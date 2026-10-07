"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";

const ForceGraph2D = dynamic(
  () => import("react-force-graph-2d"),
  {
    ssr: false,
  }
);


// ============================================================
// Types
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
  file?: string;
  line?: number;
  language?: string;
}

interface GraphEdge {
  source: string | GraphNode;
  target: string | GraphNode;
  type: EdgeType;
  line?: number;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  adjacency: Record<
    string,
    Record<string, string[]>
  >;
  repository: {
    owner: string;
    name: string;
    full_name: string;
    default_branch: string;
    stars: number;
    language?: string;
  };
  stats: {
    nodes: number;
    edges: number;
    files_analyzed: number;
    symbols: number;
    node_types: Record<string, number>;
    edge_types: Record<string, number>;
  };
}

interface AnalysisResponse {
  analysis_id: string;
  status: string;
  repository: GraphData["repository"];
  stats: GraphData["stats"];
}


// ============================================================
// Constants
// ============================================================

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

const NODE_COLORS: Record<string, string> = {
  repository: "#f97316",
  directory: "#a855f7",
  file: "#3b82f6",
  class: "#22c55e",
  interface: "#14b8a6",
  function: "#eab308",
  method: "#ec4899",
};

const EDGE_COLORS: Record<string, string> = {
  contains: "#64748b",
  imports: "#3b82f6",
  calls: "#22c55e",
  inherits: "#a855f7",
  implements: "#14b8a6",
  instantiates: "#eab308",
  depends: "#ef4444",
};


// ============================================================
// Main component
// ============================================================

export default function Home() {

  const [repoUrl, setRepoUrl] = useState("");

  const [maxFiles, setMaxFiles] = useState(500);

  const [analysisId, setAnalysisId] =
    useState<string | null>(null);

  const [graph, setGraph] =
    useState<GraphData | null>(null);

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState("");

  const [selectedNode, setSelectedNode] =
    useState<GraphNode | null>(null);

  const [search, setSearch] =
    useState("");

  const [visibleTypes, setVisibleTypes] =
    useState<Record<string, boolean>>({
      repository: true,
      directory: true,
      file: true,
      class: true,
      interface: true,
      function: true,
      method: true,
    });

  const [visibleEdges, setVisibleEdges] =
    useState<Record<string, boolean>>({
      contains: true,
      imports: true,
      calls: true,
      inherits: true,
      implements: true,
      instantiates: true,
      depends: true,
    });


  // ==========================================================
  // Analyze
  // ==========================================================

  const analyzeRepository = async () => {

    if (!repoUrl.trim()) {
      setError("Enter a GitHub repository URL.");
      return;
    }

    setLoading(true);
    setError("");
    setGraph(null);
    setSelectedNode(null);

    try {

      const response = await fetch(
        `${API_URL}/api/analyze`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            repo_url: repoUrl,
            max_files: maxFiles,
          }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail ||
          "Analysis failed."
        );
      }

      setAnalysisId(
        data.analysis_id
      );

      const graphResponse =
        await fetch(
          `${API_URL}/api/analysis/${data.analysis_id}/graph`
        );

      const graphData =
        await graphResponse.json();

      if (!graphResponse.ok) {
        throw new Error(
          graphData.detail ||
          "Could not retrieve graph."
        );
      }

      setGraph(graphData);

    } catch (err) {

      setError(
        err instanceof Error
          ? err.message
          : "Something went wrong."
      );

    } finally {
      setLoading(false);
    }
  };


  // ==========================================================
  // Keyboard shortcut
  // ==========================================================

  useEffect(() => {

    const handler = (event: KeyboardEvent) => {

      if (
        event.key === "Enter" &&
        (event.ctrlKey || event.metaKey)
      ) {
        analyzeRepository();
      }
    };

    window.addEventListener(
      "keydown",
      handler
    );

    return () => {
      window.removeEventListener(
        "keydown",
        handler
      );
    };

  }, [repoUrl, maxFiles]);


  // ==========================================================
  // Filter graph
  // ==========================================================

  const filteredGraph = useMemo(() => {

    if (!graph) {
      return {
        nodes: [],
        links: [],
      };
    }

    const query =
      search.trim().toLowerCase();

    const visibleNodeIds = new Set(
      graph.nodes
        .filter((node) => {

          if (!visibleTypes[node.type]) {
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
              .includes(query)
          );
        })
        .map((node) => node.id)
    );

    const nodes =
      graph.nodes.filter(
        (node) =>
          visibleNodeIds.has(node.id)
      );

    const links =
      graph.edges
        .filter((edge) => {

          if (!visibleEdges[edge.type]) {
            return false;
          }

          const source =
            typeof edge.source === "string"
              ? edge.source
              : edge.source.id;

          const target =
            typeof edge.target === "string"
              ? edge.target
              : edge.target.id;

          return (
            visibleNodeIds.has(source) &&
            visibleNodeIds.has(target)
          );
        })
        .map((edge) => ({
          ...edge,
          source:
            typeof edge.source === "string"
              ? edge.source
              : edge.source.id,
          target:
            typeof edge.target === "string"
              ? edge.target
              : edge.target.id,
        }));

    return {
      nodes,
      links,
    };

  }, [
    graph,
    search,
    visibleTypes,
    visibleEdges,
  ]);


  // ==========================================================
  // Toggle helpers
  // ==========================================================

  const toggleNodeType =
    useCallback((type: string) => {

      setVisibleTypes((previous) => ({
        ...previous,
        [type]: !previous[type],
      }));

    }, []);


  const toggleEdgeType =
    useCallback((type: string) => {

      setVisibleEdges((previous) => ({
        ...previous,
        [type]: !previous[type],
      }));

    }, []);


  // ==========================================================
  // Render
  // ==========================================================

  return (
    <main className="min-h-screen bg-[#09090b] text-white">

      {/* =====================================================
          Header
      ====================================================== */}

      <header className="border-b border-white/10">

        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-6 py-4">

          <div>
            <h1 className="text-xl font-semibold">
              CodeAtlas
            </h1>

            <p className="text-xs text-zinc-500">
              Static code intelligence
            </p>
          </div>

          {graph && (
            <div className="text-xs text-zinc-500">
              {graph.repository.full_name}
            </div>
          )}

        </div>

      </header>


      {/* =====================================================
          Repository input
      ====================================================== */}

      <section className="border-b border-white/10 bg-[#0c0c0f]">

        <div className="mx-auto max-w-[1600px] px-6 py-5">

          <div className="flex flex-col gap-3 md:flex-row">

            <input
              value={repoUrl}
              onChange={(event) =>
                setRepoUrl(event.target.value)
              }
              placeholder="https://github.com/owner/repository"
              className="flex-1 rounded-lg border border-white/10 bg-[#151518] px-4 py-3 text-sm outline-none transition focus:border-blue-500"
            />

            <input
              type="number"
              min={1}
              max={5000}
              value={maxFiles}
              onChange={(event) =>
                setMaxFiles(
                  Number(event.target.value)
                )
              }
              title="Maximum source files"
              className="w-full rounded-lg border border-white/10 bg-[#151518] px-4 py-3 text-sm outline-none focus:border-blue-500 md:w-32"
            />

            <button
              onClick={analyzeRepository}
              disabled={loading}
              className="rounded-lg bg-white px-6 py-3 text-sm font-medium text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading
                ? "Analyzing..."
                : "Analyze"}
            </button>

          </div>

          <div className="mt-2 flex items-center justify-between">

            <p className="text-xs text-zinc-600">
              Ctrl + Enter to analyze
            </p>

            {analysisId && (
              <p className="text-xs text-zinc-600">
                Analysis:{" "}
                {analysisId.slice(0, 8)}
              </p>
            )}

          </div>

          {error && (
            <div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          )}

        </div>

      </section>


      {/* =====================================================
          Dashboard
      ====================================================== */}

      {graph && (

        <div className="mx-auto max-w-[1600px] px-6 py-6">

          {/* Stats */}

          <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">

            <Stat
              label="Files"
              value={graph.stats.files_analyzed}
            />

            <Stat
              label="Symbols"
              value={graph.stats.symbols}
            />

            <Stat
              label="Nodes"
              value={graph.stats.nodes}
            />

            <Stat
              label="Edges"
              value={graph.stats.edges}
            />

            <Stat
              label="Stars"
              value={graph.repository.stars}
            />

          </div>


          {/* =================================================
              Main workspace
          ================================================= */}

          <div className="grid min-h-[700px] grid-cols-1 overflow-hidden rounded-xl border border-white/10 bg-[#0c0c0f] lg:grid-cols-[240px_1fr_280px]">


            {/* =================================================
                Left controls
            ================================================= */}

            <aside className="border-b border-white/10 p-4 lg:border-b-0 lg:border-r">

              <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                Node types
              </p>

              <div className="space-y-2">

                {Object.keys(
                  visibleTypes
                ).map((type) => (

                  <FilterRow
                    key={type}
                    label={type}
                    checked={
                      visibleTypes[type]
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


              <div className="my-6 border-t border-white/10" />


              <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                Edge types
              </p>

              <div className="space-y-2">

                {Object.keys(
                  visibleEdges
                ).map((type) => (

                  <FilterRow
                    key={type}
                    label={type}
                    checked={
                      visibleEdges[type]
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


            {/* =================================================
                Graph
            ================================================= */}

            <section className="relative min-h-[700px]">

              <div className="absolute left-4 top-4 z-10">

                <input
                  value={search}
                  onChange={(event) =>
                    setSearch(
                      event.target.value
                    )
                  }
                  placeholder="Search nodes..."
                  className="w-64 rounded-lg border border-white/10 bg-[#151518]/90 px-3 py-2 text-xs outline-none backdrop-blur focus:border-blue-500"
                />

              </div>


              <div className="absolute bottom-4 left-4 z-10 rounded-lg border border-white/10 bg-[#151518]/90 px-3 py-2 text-xs text-zinc-500 backdrop-blur">

                {filteredGraph.nodes.length}
                {" "}nodes ·{" "}
                {filteredGraph.links.length}
                {" "}edges

              </div>


              <ForceGraph2D
                graphData={filteredGraph}

                nodeId="id"

                nodeLabel={(node: any) => {

                  const n =
                    node as GraphNode;

                  return `
                    <div style="
                      padding: 6px 8px;
                      background: #18181b;
                      border: 1px solid #3f3f46;
                      border-radius: 6px;
                      color: white;
                      font-family: sans-serif;
                      font-size: 12px;
                    ">
                      <strong>${escapeHtml(
                        n.name
                      )}</strong>
                      <br/>
                      <span style="
                        color: #a1a1aa;
                      ">
                        ${escapeHtml(
                          n.type
                        )}
                      </span>
                      ${
                        n.file
                          ? `<br/><span style="color:#71717a">${escapeHtml(n.file)}</span>`
                          : ""
                      }
                    </div>
                  `;
                }}

                nodeColor={(node: any) =>
                  NODE_COLORS[
                    (node as GraphNode).type
                  ] || "#ffffff"
                }

                nodeRelSize={5}

                nodeVal={(node: any) => {

                  const type =
                    (node as GraphNode).type;

                  if (type === "repository")
                    return 12;

                  if (type === "directory")
                    return 8;

                  if (type === "file")
                    return 6;

                  return 4;
                }}

                linkColor={(link: any) =>
                  EDGE_COLORS[
                    link.type
                  ] || "#52525b"
                }

                linkWidth={(link: any) =>
                  link.type === "depends"
                    ? 2
                    : 1
                }

                linkDirectionalArrowLength={4}

                linkDirectionalArrowRelPos={1}

                linkCurvature={0.12}

                onNodeClick={(node: any) => {

                  setSelectedNode(
                    node as GraphNode
                  );

                }}

                onBackgroundClick={() => {
                  setSelectedNode(null);
                }}

                nodeCanvasObject={(
                  node: any,
                  ctx: CanvasRenderingContext2D,
                  globalScale: number
                ) => {

                  const n =
                    node as GraphNode;

                  const label =
                    n.name;

                  const fontSize =
                    Math.max(
                      9 / globalScale,
                      2
                    );

                  const radius =
                    n.type === "repository"
                      ? 7
                      : n.type === "directory"
                        ? 5
                        : 4;

                  ctx.beginPath();

                  ctx.arc(
                    node.x,
                    node.y,
                    radius,
                    0,
                    2 * Math.PI
                  );

                  ctx.fillStyle =
                    NODE_COLORS[
                      n.type
                    ] || "#ffffff";

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
                      "#d4d4d8";

                    ctx.fillText(
                      label,
                      node.x,
                      node.y + radius + 2
                    );
                  }

                }}

                cooldownTicks={150}

                d3AlphaDecay={0.03}

                d3VelocityDecay={0.35}

                warmupTicks={50}

                enableNodeDrag={true}

                enableZoomInteraction={true}

                enablePanInteraction={true}

                width={undefined}

                height={700}

              />

            </section>


            {/* =================================================
                Inspector
            ================================================= */}

            <aside className="border-t border-white/10 p-5 lg:border-l lg:border-t-0">

              <p className="mb-5 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                Inspector
              </p>

              {!selectedNode ? (

                <div className="text-sm text-zinc-600">
                  Click a node to inspect it.
                </div>

              ) : (

                <NodeInspector
                  node={selectedNode}
                  graph={graph}
                />

              )}

            </aside>

          </div>

        </div>

      )}


      {/* =====================================================
          Empty state
      ====================================================== */}

      {!graph && !loading && (

        <section className="flex min-h-[600px] items-center justify-center px-6">

          <div className="max-w-lg text-center">

            <div className="mb-5 text-5xl">
              ◈
            </div>

            <h2 className="text-2xl font-semibold">
              Explore a codebase
            </h2>

            <p className="mt-3 text-sm leading-6 text-zinc-500">
              Enter a public GitHub repository and
              CodeAtlas will parse its Python, Java,
              JavaScript and TypeScript source code
              into an interactive master graph.
            </p>

            <div className="mt-6 text-xs text-zinc-700">
              Functions · Classes · Imports · Calls ·
              Dependencies · Inheritance
            </div>

          </div>

        </section>

      )}

    </main>
  );
}


// ============================================================
// Components
// ============================================================

function Stat({
  label,
  value,
}: {
  label: string;
  value: number;
}) {

  return (
    <div className="rounded-lg border border-white/10 bg-[#0c0c0f] px-4 py-3">

      <p className="text-xs text-zinc-600">
        {label}
      </p>

      <p className="mt-1 text-xl font-semibold">
        {value.toLocaleString()}
      </p>

    </div>
  );
}


function FilterRow({
  label,
  checked,
  color,
  onClick,
}: {
  label: string;
  checked: boolean;
  color: string;
  onClick: () => void;
}) {

  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition hover:bg-white/5"
    >

      <span
        className="h-2.5 w-2.5 rounded-full"
        style={{
          backgroundColor: checked
            ? color
            : "#3f3f46",
        }}
      />

      <span
        className={
          checked
            ? "text-zinc-300"
            : "text-zinc-600"
        }
      >
        {label}
      </span>

    </button>
  );
}


function NodeInspector({
  node,
  graph,
}: {
  node: GraphNode;
  graph: GraphData;
}) {

  const outgoing =
    graph.edges.filter(
      (edge) => {

        const source =
          typeof edge.source === "string"
            ? edge.source
            : edge.source.id;

        return source === node.id;
      }
    );

  const incoming =
    graph.edges.filter(
      (edge) => {

        const target =
          typeof edge.target === "string"
            ? edge.target
            : edge.target.id;

        return target === node.id;
      }
    );

  return (
    <div>

      <div
        className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg"
        style={{
          backgroundColor:
            `${NODE_COLORS[node.type]}22`,
          color:
            NODE_COLORS[node.type],
        }}
      >
        ◈
      </div>


      <h2 className="break-words text-lg font-semibold">
        {node.name}
      </h2>


      <p className="mt-1 text-xs text-zinc-500">
        {node.type}
      </p>


      {node.file && (

        <div className="mt-5">

          <p className="text-[10px] uppercase tracking-wider text-zinc-600">
            File
          </p>

          <p className="mt-1 break-all font-mono text-xs text-zinc-400">
            {node.file}
          </p>

        </div>

      )}


      {node.line && (

        <div className="mt-4">

          <p className="text-[10px] uppercase tracking-wider text-zinc-600">
            Line
          </p>

          <p className="mt-1 text-xs text-zinc-400">
            {node.line}
          </p>

        </div>

      )}


      {node.language && (

        <div className="mt-4">

          <p className="text-[10px] uppercase tracking-wider text-zinc-600">
            Language
          </p>

          <p className="mt-1 text-xs text-zinc-400">
            {node.language}
          </p>

        </div>

      )}


      <div className="my-5 border-t border-white/10" />


      <ConnectionList
        title="Outgoing"
        edges={outgoing}
        graph={graph}
      />


      <ConnectionList
        title="Incoming"
        edges={incoming}
        graph={graph}
      />

    </div>
  );
}


function ConnectionList({
  title,
  edges,
  graph,
}: {
  title: string;
  edges: GraphEdge[];
  graph: GraphData;
}) {

  if (!edges.length) {
    return null;
  }

  return (
    <div className="mb-5">

      <p className="mb-2 text-[10px] uppercase tracking-wider text-zinc-600">
        {title}
      </p>

      <div className="space-y-2">

        {edges.slice(0, 20).map(
          (edge, index) => {

            const id =
              typeof edge.target === "string"
                ? edge.target
                : edge.target.id;

            const target =
              graph.nodes.find(
                (node) =>
                  node.id === id
              );

            return (
              <div
                key={`${edge.type}-${id}-${index}`}
                className="rounded-md border border-white/5 bg-white/[0.02] px-2 py-2"
              >

                <p
                  className="text-[10px]"
                  style={{
                    color:
                      EDGE_COLORS[
                        edge.type
                      ],
                  }}
                >
                  {edge.type}
                </p>

                <p className="mt-1 break-all text-xs text-zinc-400">
                  {target?.name || id}
                </p>

              </div>
            );

          }
        )}

      </div>

    </div>
  );
}


// ============================================================
// HTML escaping for graph tooltip
// ============================================================

function escapeHtml(
  value: string
) {

  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}