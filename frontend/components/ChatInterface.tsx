'use client';
import { useState, useEffect, useRef } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import GraphViz, { GraphData } from './GraphViz';

interface Message {
    role: string;
    content: string;
    dataset?: string;
}

// Pipeline stages in order
type PipelineStage = 'idle' | 'retrieve' | 'grade' | 'rewrite' | 'generate' | 'done';

const STAGES: { id: PipelineStage; label: string; icon: string }[] = [
    { id: 'retrieve',  label: 'Retrieve',  icon: '⬡' },
    { id: 'grade',     label: 'Grade',     icon: '⬡' },
    { id: 'rewrite',   label: 'Rewrite',   icon: '⬡' },
    { id: 'generate',  label: 'Generate',  icon: '⬡' },
];

const STAGE_ORDER: PipelineStage[] = ['retrieve', 'grade', 'rewrite', 'generate', 'done'];

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8005';

const DATASET_COLORS: Record<string, { btn: string; badge: string }> = {
    book:                        { btn: 'bg-purple-600',  badge: 'bg-purple-900/60 text-purple-300'   },
    papers_energy_sustainability:{ btn: 'bg-emerald-600', badge: 'bg-emerald-900/60 text-emerald-300' },
    papers_serious_games:        { btn: 'bg-amber-600',   badge: 'bg-amber-900/60 text-amber-300'     },
    papers_hci_ubicomp:          { btn: 'bg-cyan-600',    badge: 'bg-cyan-900/60 text-cyan-300'       },
    default:                     { btn: 'bg-blue-600',    badge: 'bg-blue-900/60 text-blue-300'       },
};

function getDatasetColor(ds: string) {
    return DATASET_COLORS[ds] ?? { btn: 'bg-slate-600', badge: 'bg-slate-700 text-slate-300' };
}

// Compact dataset labels for the button strip
function shortLabel(ds: string) {
    return ds
        .replace('papers_energy_sustainability', 'energy')
        .replace('papers_serious_games', 'games')
        .replace('papers_hci_ubicomp', 'hci')
        .replace('default', 'default');
}

export default function ChatInterface() {
    const [query, setQuery]               = useState('');
    const [messages, setMessages]         = useState<Message[]>([]);
    const [graphData, setGraphData]       = useState<GraphData>({ nodes: [], links: [] });
    const [status, setStatus]             = useState('Idle');
    const [isStreaming, setIsStreaming]   = useState(false);
    const [nodeCount, setNodeCount]       = useState(0);
    const [linkCount, setLinkCount]       = useState(0);
    const [datasets, setDatasets]         = useState<string[]>(['default']);
    const [selectedDataset, setSelectedDataset] = useState<string>('default');
    const [activeStage, setActiveStage]   = useState<PipelineStage>('idle');
    const [completedStages, setCompletedStages] = useState<Set<PipelineStage>>(new Set());
    const [highlightNodeIds, setHighlightNodeIds] = useState<Set<string>>(new Set());
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // Auto-scroll chat
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    // Fetch available datasets on mount
    useEffect(() => {
        const fetchDatasets = async () => {
            try {
                const res  = await fetch(`${API_URL}/datasets`);
                const data = await res.json();
                if (data.datasets?.length > 0) {
                    setDatasets(data.datasets);
                    setSelectedDataset(data.datasets[0]);
                }
            } catch { /* keep default */ }
        };
        fetchDatasets();
    }, []);

    // Load graph whenever selected dataset changes
    useEffect(() => {
        const loadGraph = async () => {
            setStatus(`Loading graph for [${selectedDataset}]...`);
            try {
                const res  = await fetch(`${API_URL}/graph?dataset_id=${encodeURIComponent(selectedDataset)}`);
                const data: GraphData = await res.json();
                if (data.nodes.length > 0) {
                    setGraphData(data);
                    setNodeCount(data.nodes.length);
                    setLinkCount(data.links.length);
                    setStatus(`[${selectedDataset}] — ${data.nodes.length} entities, ${data.links.length} relationships`);
                } else {
                    setGraphData({ nodes: [], links: [] });
                    setNodeCount(0);
                    setLinkCount(0);
                    setStatus(`[${selectedDataset}] No graph data yet — ingest documents first`);
                }
            } catch {
                setStatus('Backend not reachable — start docker-compose');
            }
        };
        loadGraph();
        // Reset stage on dataset switch
        setActiveStage('idle');
        setCompletedStages(new Set());
        setHighlightNodeIds(new Set());
    }, [selectedDataset]);

    const handleSearch = async () => {
        if (!query.trim() || isStreaming) return;

        const currentQuery   = query;
        const currentDataset = selectedDataset;
        setQuery('');
        setIsStreaming(true);
        setActiveStage('idle');
        setCompletedStages(new Set());
        setHighlightNodeIds(new Set());
        setMessages(prev => [
            ...prev,
            { role: 'user',      content: currentQuery,   dataset: currentDataset },
            { role: 'assistant', content: '',              dataset: currentDataset },
        ]);
        setStatus('Connecting to agent...');

        const ctrl = new AbortController();

        await fetchEventSource(
            `${API_URL}/stream?query=${encodeURIComponent(currentQuery)}&dataset_id=${encodeURIComponent(currentDataset)}`,
            {
                signal: ctrl.signal,
                onmessage(msg) {
                    if (msg.event === 'message') {
                        setMessages(prev => {
                            const updated   = [...prev];
                            const lastIndex = updated.length - 1;
                            if (lastIndex >= 0 && updated[lastIndex].role === 'assistant') {
                                updated[lastIndex] = {
                                    ...updated[lastIndex],
                                    content: updated[lastIndex].content + msg.data,
                                };
                            }
                            return updated;
                        });

                    } else if (msg.event === 'graph_update') {
                        const payload: GraphData = JSON.parse(msg.data);
                        setGraphData(payload);
                        setNodeCount(payload.nodes.length);
                        setLinkCount(payload.links.length);
                        // Highlight the retrieved chunk nodes
                        const chunkIds = new Set(
                            payload.nodes
                                .filter(n => n.type === 'Chunk')
                                .map(n => n.id)
                        );
                        setHighlightNodeIds(chunkIds);

                    } else if (msg.event === 'status') {
                        const raw = msg.data;
                        if (raw.startsWith('stage:')) {
                            const stage = raw.replace('stage:', '') as PipelineStage;
                            setActiveStage(stage);
                            // Mark all stages before this one as completed
                            const idx = STAGE_ORDER.indexOf(stage);
                            setCompletedStages(prev => {
                                const next = new Set(prev);
                                STAGE_ORDER.slice(0, idx).forEach(s => next.add(s));
                                return next;
                            });
                            setStatus(`Running: ${stage}...`);
                        } else {
                            setStatus(raw);
                        }
                    }
                },
                onclose() {
                    setIsStreaming(false);
                    setActiveStage('done');
                    setCompletedStages(new Set(STAGE_ORDER));
                    setStatus('Done ✓');
                    ctrl.abort();
                },
                onerror(err: any) {
                    console.error('Stream failed:', err);
                    setStatus('Error connecting to agent.');
                    setIsStreaming(false);
                    setActiveStage('idle');
                    ctrl.abort();
                    throw err;
                },
            }
        );
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSearch();
        }
    };

    return (
        <div className="flex flex-row h-screen p-4 bg-slate-950 gap-4">

            {/* ── Left: Chat Panel ─────────────────────────────── */}
            <div className="w-[380px] flex-shrink-0 flex flex-col gap-3">

                {/* Header */}
                <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${isStreaming ? 'bg-blue-400 animate-pulse' : 'bg-green-400'}`} />
                    <h1 className="text-white font-semibold text-sm tracking-wide">EnterpriseBrain</h1>
                    <span className="ml-auto text-xs text-slate-500 font-mono">{nodeCount}n · {linkCount}e</span>
                </div>

                {/* Dataset Selector */}
                <div className="flex items-start gap-2">
                    <span className="text-xs text-slate-400 flex-shrink-0 pt-1">Dataset:</span>
                    <div className="flex gap-1 flex-wrap">
                        {datasets.map(ds => {
                            const col    = getDatasetColor(ds);
                            const active = selectedDataset === ds;
                            return (
                                <button
                                    key={ds}
                                    onClick={() => setSelectedDataset(ds)}
                                    disabled={isStreaming}
                                    className={`px-2 py-1 rounded text-xs font-mono font-semibold transition-all
                                        ${active ? `${col.btn} text-white ring-1 ring-white/20` : 'bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-white'}
                                        disabled:opacity-40 disabled:cursor-not-allowed`}
                                >
                                    {shortLabel(ds)}
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* ── Pipeline Stage Visualizer ── */}
                <div className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
                    <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">Pipeline</div>
                    <div className="flex items-center gap-1">
                        {STAGES.map((s, idx) => {
                            const isActive    = activeStage === s.id;
                            const isCompleted = completedStages.has(s.id);
                            const isIdle      = activeStage === 'idle' || activeStage === 'done';

                            return (
                                <div key={s.id} className="flex items-center gap-1">
                                    {/* Stage pill */}
                                    <div className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono font-medium transition-all duration-300
                                        ${isActive    ? 'bg-blue-600/80 text-white ring-1 ring-blue-400/60 shadow-[0_0_8px_rgba(59,130,246,0.5)]' : ''}
                                        ${isCompleted && !isActive ? 'bg-slate-700/60 text-slate-400' : ''}
                                        ${!isActive && !isCompleted ? 'bg-slate-800/60 text-slate-600' : ''}
                                    `}>
                                        {/* Spinner for active, check for done, dot for pending */}
                                        {isActive ? (
                                            <span className="inline-block w-2 h-2 rounded-full border border-blue-300 border-t-transparent animate-spin" />
                                        ) : isCompleted ? (
                                            <span className="text-emerald-400 text-[10px]">✓</span>
                                        ) : (
                                            <span className="w-1.5 h-1.5 rounded-full bg-slate-700 inline-block" />
                                        )}
                                        {s.label}
                                    </div>
                                    {/* Connector arrow (not after last) */}
                                    {idx < STAGES.length - 1 && (
                                        <span className={`text-[10px] transition-colors duration-300 ${
                                            isCompleted ? 'text-slate-500' : 'text-slate-700'
                                        }`}>→</span>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* Messages */}
                <div className="flex-1 overflow-y-auto flex flex-col gap-3 pr-1">
                    {messages.length === 0 && (
                        <div className="text-slate-600 text-sm text-center mt-8">
                            Ask a question about the selected dataset
                        </div>
                    )}
                    {messages.map((m, i) => (
                        <div key={i} className="flex flex-col gap-1">
                            {m.role === 'user' && m.dataset && (
                                <span className={`self-end text-[10px] font-mono px-1.5 py-0.5 rounded ${getDatasetColor(m.dataset).badge}`}>
                                    {shortLabel(m.dataset)}
                                </span>
                            )}
                            <div className={`rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
                                m.role === 'user'
                                    ? 'bg-blue-900/60 text-blue-100 ml-6'
                                    : 'bg-slate-800 text-slate-100 mr-6'
                            }`}>
                                {m.content || (
                                    <span className="text-slate-500 italic animate-pulse">Thinking…</span>
                                )}
                            </div>
                        </div>
                    ))}
                    <div ref={messagesEndRef} />
                </div>

                {/* Status bar */}
                <div className="text-xs text-blue-400 font-mono bg-slate-900 rounded px-2 py-1.5 border border-slate-800 truncate">
                    {status}
                </div>

                {/* Input */}
                <div className="flex gap-2">
                    <input
                        className="flex-1 bg-slate-800 text-white border border-slate-700 px-3 py-2 rounded text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder={`Ask about [${shortLabel(selectedDataset)}]…`}
                        disabled={isStreaming}
                    />
                    <button
                        onClick={handleSearch}
                        disabled={isStreaming || !query.trim()}
                        className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-semibold hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                        {isStreaming ? '…' : 'Ask'}
                    </button>
                </div>
            </div>

            {/* ── Right: Graph Panel ───────────────────────────── */}
            <div className="flex-1 flex flex-col gap-2 min-w-0">
                <div className="flex items-center gap-2">
                    <h2 className="text-white text-sm font-semibold">Live Knowledge Graph</h2>
                    <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${getDatasetColor(selectedDataset).badge}`}>
                        {shortLabel(selectedDataset)}
                    </span>
                    {highlightNodeIds.size > 0 && (
                        <span className="text-xs text-blue-400 font-mono animate-pulse">
                            ↳ {highlightNodeIds.size} chunks retrieved
                        </span>
                    )}
                    <span className="ml-auto text-xs text-slate-600">
                        entity nodes colored by type · click a node to inspect
                    </span>
                </div>
                <div className="flex-1 min-h-0">
                    <GraphViz data={graphData} highlightNodeIds={highlightNodeIds} />
                </div>
            </div>
        </div>
    );
}
