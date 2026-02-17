'use client';
import { useState } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import GraphViz from './GraphViz';

interface Message {
    role: string;
    content: string;
}

interface GraphData {
    nodes: { id: string; label: string; val: number; text?: string }[];
    links: { source: string; target: string }[];
}

export default function ChatInterface() {
    const [query, setQuery] = useState("");
    const [messages, setMessages] = useState<Message[]>([]);
    const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] });
    const [status, setStatus] = useState("Idle");

    const handleSearch = async () => {
        if (!query) return;

        // Optimistic UI updates
        setMessages(prev => [...prev, { role: 'user', content: query }]);
        setMessages(prev => [...prev, { role: 'assistant', content: '' }]);
        setStatus("Connecting to Agent...");

        await fetchEventSource(`http://localhost:8000/stream?query=${encodeURIComponent(query)}`, {
            onmessage(msg) {
                // Handle the custom events we defined in FastAPI
                if (msg.event === 'message') {
                    // Update the last message with the new token
                    setMessages(prev => {
                        const newMsgs = [...prev];
                        const lastMsg = newMsgs[newMsgs.length - 1];
                        lastMsg.content += msg.data;
                        return newMsgs;
                    });
                }
                else if (msg.event === 'graph_update') {
                    // Update the visualization
                    const payload = JSON.parse(msg.data);
                    setGraphData(payload);
                }
                else if (msg.event === 'status') {
                    setStatus(msg.data);
                }
            },
            onerror(err) {
                console.error("Stream failed:", err);
                setStatus("Error connecting to agent.");
            }
        });
    };

    return (
        <div className="flex flex-row h-screen p-6 bg-slate-950 gap-6">
            {/* Chat Panel */}
            <div className="w-1/3 flex flex-col gap-4">
                <div className="flex-1 overflow-y-auto bg-slate-900 rounded p-4 border border-slate-800">
                    {messages.map((m, i) => (
                        <div key={i} className={`mb-4 p-3 rounded ${m.role === 'user' ? 'bg-blue-900 self-end' : 'bg-slate-800 self-start'}`}>
                            <p className="text-white whitespace-pre-wrap">{m.content}</p>
                        </div>
                    ))}
                </div>
                <div className="text-sm text-blue-400 font-mono">STATUS: {status}</div>
                <div className="flex gap-2">
                    <input
                        className="flex-1 bg-slate-800 text-white border border-slate-700 p-2 rounded"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Ask the Knowledge Graph..."
                    />
                    <button onClick={handleSearch} className="bg-blue-600 text-white px-4 py-2 rounded font-bold hover:bg-blue-500">
                        Ask
                    </button>
                </div>
            </div>

            {/* Graph Visualization Panel */}
            <div className="w-2/3 flex flex-col">
                <h2 className="text-white text-xl font-bold mb-4">Live Graph Context</h2>
                <GraphViz data={graphData} />
            </div>
        </div>
    );
}
