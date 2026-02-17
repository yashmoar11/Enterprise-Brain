'use client';

import dynamic from 'next/dynamic';
import { useMemo } from 'react';

// Dynamic import to disable SSR for the graph component
const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), {
    ssr: false,
    loading: () => <div className="text-gray-500">Loading Graph Engine...</div>
});

interface GraphData {
    nodes: { id: string; label: string; val: number; text?: string }[];
    links: { source: string; target: string }[];
}

export default function GraphViz({ data }: { data: GraphData }) {
    // Memoize data to prevent graph flickering on every render
    const graphData = useMemo(() => {
        return data.nodes.length > 0 ? data : { nodes: [], links: [] };
    }, [data]);

    return (
        <div className="w-full h-[600px] bg-slate-900 rounded-lg border border-slate-700 overflow-hidden">
            <ForceGraph2D
                graphData={graphData}
                nodeLabel="text"
                nodeColor={() => "#4ade80"} // Neo4j Green
                backgroundColor="#0f172a"   // Slate 900
                nodeRelSize={6}
                linkWidth={2}
                linkColor={() => "#94a3b8"}
            />
        </div>
    );
}
