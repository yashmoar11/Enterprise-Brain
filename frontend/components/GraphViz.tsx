'use client';

import dynamic from 'next/dynamic';
import { useMemo, useCallback, useRef, useEffect, useState } from 'react';
import { ForceGraphMethods } from 'react-force-graph-2d';

const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), {
    ssr: false,
    loading: () => (
        <div className="w-full h-full flex items-center justify-center text-slate-500 text-sm">
            Loading Graph Engine...
        </div>
    )
});

export interface GraphNode {
    id: string;
    label: string;
    type: string;
    val?: number;
}

export interface GraphLink {
    source: string | GraphNode;
    target: string | GraphNode;
    label?: string;
}

export interface GraphData {
    nodes: GraphNode[];
    links: GraphLink[];
}

const NODE_COLORS: Record<string, string> = {
    // People & orgs
    Person:            '#60a5fa',  // blue
    Author:            '#60a5fa',
    Organization:      '#fb923c',  // orange
    Institution:       '#fb923c',
    Company:           '#fb923c',
    // ML / tech
    Algorithm:         '#34d399',  // emerald
    Method:            '#34d399',
    ModelArchitecture: '#34d399',
    Concept:           '#a78bfa',  // violet
    SoftwareFramework: '#22d3ee',  // cyan
    Technology:        '#22d3ee',
    Hardware:          '#f472b6',  // pink
    PerformanceMetric: '#facc15',  // yellow
    // Research
    Paper:             '#c084fc',  // purple
    Dataset:           '#fbbf24',  // amber
    Result:            '#4ade80',  // green
    Hypothesis:        '#e879f9',  // fuchsia
    ResearchField:     '#e879f9',
    OptimizationTechnique: '#34d399',
    // Special
    Query:             '#ffffff',
    Chunk:             '#475569',
    Entity:            '#4ade80',
};

const LEGEND_ENTRIES = [
    { type: 'Person / Author',     color: '#60a5fa' },
    { type: 'Organization',        color: '#fb923c' },
    { type: 'Algorithm / Method',  color: '#34d399' },
    { type: 'Concept',             color: '#a78bfa' },
    { type: 'Framework / Tech',    color: '#22d3ee' },
    { type: 'Paper',               color: '#c084fc' },
    { type: 'Query',               color: '#ffffff' },
    { type: 'Retrieved Chunk',     color: '#475569' },
];

export default function GraphViz({
    data,
    highlightNodeIds,
}: {
    data: GraphData;
    highlightNodeIds?: Set<string>;
}) {
    const fgRef = useRef<ForceGraphMethods | undefined>(undefined);
    const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

    // Degree map — number of connections per node
    const nodeDegree = useMemo(() => {
        const d: Record<string, number> = {};
        data.links.forEach(link => {
            const s = typeof link.source === 'object' ? (link.source as GraphNode).id : link.source as string;
            const t = typeof link.target === 'object' ? (link.target as GraphNode).id : link.target as string;
            d[s] = (d[s] || 0) + 1;
            d[t] = (d[t] || 0) + 1;
        });
        return d;
    }, [data.links]);

    // Enrich nodes with degree-based sizing
    const graphData = useMemo(() => {
        if (!data?.nodes?.length) return { nodes: [], links: [] };
        return {
            nodes: data.nodes.map(n => ({
                ...n,
                val: n.type === 'Query' ? 5
                   : n.type === 'Chunk' ? 2
                   : 1 + Math.min((nodeDegree[n.id] || 0) * 0.5, 5),
            })),
            links: data.links,
        };
    }, [data, nodeDegree]);

    // Physics tuning
    useEffect(() => {
        const fg = fgRef.current;
        if (fg) {
            fg.d3Force('charge')?.strength(-280);
            fg.d3Force('link')?.distance(75);
            fg.d3Force('center')?.strength(0.04);
        }
    }, [graphData]);

    // Zoom to fit when data changes
    useEffect(() => {
        if (fgRef.current && graphData.nodes.length > 0) {
            setTimeout(() => fgRef.current?.zoomToFit(500, 50), 600);
        }
    }, [graphData]);

    // Canvas draw — circles with glow, labels, highlight rings
    const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
        const isHighlighted = highlightNodeIds?.has(node.id) ?? false;
        const isSelected    = selectedNode?.id === node.id;
        const isQuery       = node.type === 'Query';
        const isChunk       = node.type === 'Chunk';

        const radius = Math.sqrt(node.val ?? 1) * 5;
        const color  = NODE_COLORS[node.type] ?? NODE_COLORS['Entity'];
        const label  = (node.label ?? node.id) as string;

        // --- Glow ---
        if (isQuery || isHighlighted || isSelected) {
            ctx.shadowColor = isQuery ? 'rgba(255,255,255,0.9)' : color;
            ctx.shadowBlur  = isSelected ? 28 : isHighlighted ? 20 : 14;
        }

        // --- Fill ---
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
        ctx.fillStyle = isChunk && !isHighlighted
            ? 'rgba(71,85,105,0.65)'
            : color;
        ctx.fill();
        ctx.shadowBlur = 0;

        // --- Outer ring for selected / highlighted ---
        if (isSelected || isHighlighted) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius + 3 / globalScale, 0, 2 * Math.PI);
            ctx.strokeStyle = color;
            ctx.lineWidth   = 2 / globalScale;
            ctx.stroke();
        }

        // --- White border for query node ---
        if (isQuery) {
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
            ctx.strokeStyle = 'rgba(255,255,255,0.9)';
            ctx.lineWidth   = 2.5 / globalScale;
            ctx.stroke();
        }

        // --- Label (only when zoomed in enough) ---
        if (globalScale >= 0.45) {
            const fontSize = Math.max(10 / globalScale, 2.5);
            ctx.font          = `${isQuery ? 'bold ' : ''}${fontSize}px Sans-Serif`;
            ctx.textAlign     = 'center';
            ctx.textBaseline  = 'top';
            ctx.fillStyle     = isChunk && !isHighlighted
                ? 'rgba(148,163,184,0.55)'
                : '#e2e8f0';
            const display = label.length > 24 ? label.slice(0, 22) + '…' : label;
            ctx.fillText(display, node.x, node.y + radius + 2 / globalScale);
        }
    }, [highlightNodeIds, selectedNode]);

    const handleNodeClick = useCallback((node: any) => {
        setSelectedNode(prev => prev?.id === node.id ? null : (node as GraphNode));
    }, []);

    // Connections for the selected node detail panel
    const connections = useMemo(() => {
        if (!selectedNode) return [];
        return data.links
            .filter(l => {
                const s = typeof l.source === 'object' ? (l.source as GraphNode).id : l.source;
                const t = typeof l.target === 'object' ? (l.target as GraphNode).id : l.target;
                return s === selectedNode.id || t === selectedNode.id;
            })
            .map(l => {
                const sid = typeof l.source === 'object' ? (l.source as GraphNode).id : l.source as string;
                const tid = typeof l.target === 'object' ? (l.target as GraphNode).id : l.target as string;
                const otherId = sid === selectedNode.id ? tid : sid;
                const other   = data.nodes.find(n => n.id === otherId);
                const dir     = sid === selectedNode.id ? '→' : '←';
                return { rel: l.label, other, dir };
            });
    }, [selectedNode, data]);

    const isEmpty = graphData.nodes.length === 0;

    return (
        <div className="w-full h-full flex gap-2">

            {/* ── Graph canvas ─────────────────────────────────────── */}
            <div className="flex-1 min-w-0 flex flex-col gap-2">
                <div className="flex-1 bg-slate-900 rounded-lg border border-slate-700 overflow-hidden relative">
                    {isEmpty ? (
                        <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-600 gap-2">
                            <svg className="w-10 h-10 opacity-25" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <circle cx="12" cy="5"  r="2" strokeWidth="1.5"/>
                                <circle cx="5"  cy="19" r="2" strokeWidth="1.5"/>
                                <circle cx="19" cy="19" r="2" strokeWidth="1.5"/>
                                <line x1="12" y1="7" x2="5"  y2="17" strokeWidth="1.5"/>
                                <line x1="12" y1="7" x2="19" y2="17" strokeWidth="1.5"/>
                            </svg>
                            <span className="text-sm">Knowledge graph will appear here</span>
                            <span className="text-xs opacity-50">Ingest documents, then ask a question</span>
                        </div>
                    ) : (
                        <ForceGraph2D
                            ref={fgRef as any}
                            graphData={graphData}
                            nodeCanvasObject={nodeCanvasObject}
                            nodeCanvasObjectMode={() => 'replace'}
                            nodeLabel={(n: any) => `[${n.type}]  ${n.label}  (${nodeDegree[n.id] || 0} connections)`}
                            onNodeClick={handleNodeClick}
                            linkLabel={(l: any) => l.label ?? ''}
                            linkDirectionalArrowLength={5}
                            linkDirectionalArrowRelPos={1}
                            linkDirectionalParticles={3}
                            linkDirectionalParticleSpeed={0.004}
                            linkDirectionalParticleWidth={2}
                            linkCurvature={0.12}
                            backgroundColor="#0f172a"
                            linkColor={(l: any) => {
                                const s = typeof l.source === 'object' ? (l.source as any).id : l.source;
                                return s === '__query__' ? '#3b82f6' : '#1e293b';
                            }}
                            linkWidth={(l: any) => {
                                const s = typeof l.source === 'object' ? (l.source as any).id : l.source;
                                return s === '__query__' ? 2 : 1;
                            }}
                            cooldownTicks={200}
                            d3AlphaDecay={0.015}
                            d3VelocityDecay={0.38}
                        />
                    )}
                </div>

                {/* Legend */}
                <div className="flex flex-wrap gap-x-4 gap-y-1 px-1">
                    {LEGEND_ENTRIES.map(({ type, color }) => (
                        <div key={type} className="flex items-center gap-1.5">
                            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
                            <span className="text-[10px] text-slate-500">{type}</span>
                        </div>
                    ))}
                </div>
            </div>

            {/* ── Node detail panel (slides in on click) ───────────── */}
            {selectedNode && (
                <div className="w-52 flex-shrink-0 bg-slate-900 border border-slate-700 rounded-lg p-3 flex flex-col gap-3 overflow-y-auto text-sm animate-in slide-in-from-right">

                    <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold text-slate-300 tracking-wide uppercase">Node</span>
                        <button
                            onClick={() => setSelectedNode(null)}
                            className="text-slate-600 hover:text-slate-300 transition-colors text-xs leading-none"
                        >✕</button>
                    </div>

                    {/* Type pill */}
                    <div className="flex items-center gap-2">
                        <span
                            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                            style={{ backgroundColor: NODE_COLORS[selectedNode.type] ?? NODE_COLORS['Entity'] }}
                        />
                        <span className="text-xs font-mono text-slate-400">{selectedNode.type}</span>
                    </div>

                    {/* Name */}
                    <div>
                        <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-1">Name</div>
                        <div className="text-white font-medium leading-snug break-words">{selectedNode.label}</div>
                    </div>

                    {/* Degree */}
                    <div>
                        <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-1">Connections</div>
                        <div className="text-blue-400 font-mono text-lg">{nodeDegree[selectedNode.id] || 0}</div>
                    </div>

                    {/* Relationships */}
                    {connections.length > 0 && (
                        <div>
                            <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">Relationships</div>
                            <div className="flex flex-col gap-2">
                                {connections.slice(0, 8).map((c, i) => (
                                    <div key={i} className="flex flex-col gap-0.5 border-l-2 border-slate-700 pl-2">
                                        <span className="text-[10px] font-mono text-slate-500">
                                            {c.dir} {c.rel}
                                        </span>
                                        <span className="text-xs text-slate-300 truncate">
                                            {c.other?.label ?? '—'}
                                        </span>
                                    </div>
                                ))}
                                {connections.length > 8 && (
                                    <span className="text-xs text-slate-600 pl-2">
                                        +{connections.length - 8} more
                                    </span>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
