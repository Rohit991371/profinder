"use client";

import { useCallback, useEffect, useState } from "react";

type AuditEntry = { id: string; entityType: string; entityId: string; changeType: string; beforeJson: string | null; afterJson: string | null; sourceUrl: string | null; detectedAt: string; reviewedAt: string | null; reviewedBy: string | null };

export default function AdminAuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/admin/audit?q=${encodeURIComponent(query.trim())}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load the audit log.");
      setEntries(data.entries || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the audit log.");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => { void load(); }, [load]);

  return (
    <section id="audit" className="mt-12 scroll-mt-6" aria-labelledby="audit-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><h2 id="audit-heading" className="text-lg font-semibold text-[var(--navy)]">Audit log</h2><p className="mt-1 text-sm text-gray-600">Search recorded academic changes, Apply for Me status updates, and admin role changes.</p></div><button type="button" className="btn-secondary" disabled={loading} onClick={() => void load()}>Refresh</button></div>
      <form className="mt-4 flex gap-2" onSubmit={(event) => { event.preventDefault(); void load(); }}><input className="input flex-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search action, entity, ID, or admin email" aria-label="Search audit log" /><button type="submit" className="btn-primary">Search</button></form>
      {error && <p role="alert" className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{error}</p>}
      {loading ? <p className="mt-5 text-sm text-gray-500">Loading audit entries…</p> : entries.length === 0 ? <p className="mt-4 rounded-xl border border-dashed border-gray-300 p-6 text-sm text-gray-500">No audit entries found. New role changes and request updates will be recorded here when the ChangeLog table is available.</p> : (
        <div className="mt-4 space-y-3">{entries.map((entry) => <article key={entry.id} className="rounded-xl border border-gray-200 bg-white p-4"><div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div><p className="font-semibold text-[var(--navy)]">{entry.changeType}</p><p className="mt-1 text-sm text-gray-600">{entry.entityType} · {entry.entityId}</p></div><p className="text-xs text-gray-500">{new Date(entry.detectedAt).toLocaleString()}</p></div>{entry.reviewedBy && <p className="mt-2 text-xs text-gray-500">Actor: {entry.reviewedBy}</p>}{entry.beforeJson && <details className="mt-3 text-xs"><summary className="cursor-pointer font-medium text-gray-600">Before</summary><pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded bg-gray-50 p-2">{entry.beforeJson}</pre></details>}{entry.afterJson && <details className="mt-2 text-xs"><summary className="cursor-pointer font-medium text-gray-600">After / note</summary><pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded bg-gray-50 p-2">{entry.afterJson}</pre></details>}{entry.sourceUrl && <a href={entry.sourceUrl} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-[var(--teal)] hover:underline">Open source</a>}</article>)}</div>
      )}
    </section>
  );
}
