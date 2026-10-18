"use client";

import { useCallback, useEffect, useState } from "react";

type RequestItem = {
  id: string;
  status: string;
  preparationNote: string | null;
  university: { id: string; name: string; country: string; city: string | null };
};
type RequestDocument = {
  id: string;
  kind: string;
  name: string;
  size: number;
  createdAt: string;
};
type ApplyRequestRow = {
  id: string;
  status: string;
  paymentStatus: string;
  paymentRef: string | null;
  feeCents: number;
  package: string;
  studentNote: string | null;
  createdAt: string;
  updatedAt: string;
  student: { id: string; name: string | null; email: string };
  items: RequestItem[];
  documents: RequestDocument[];
  adminNote?: string;
};
type Pagination = { page: number; pageSize: number; totalCount: number; totalPages: number };
const STATUSES = [
  "draft",
  "awaiting_payment",
  "preparing",
  "needs_action",
  "ready_for_review",
  "submit_requested",
  "submitted",
  "completed",
  "cancelled",
];
const label = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const money = (cents: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);

export default function AdminApplyRequests() {
  const [requests, setRequests] = useState<ApplyRequestRow[]>([]);
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, pageSize: 20, totalCount: 0, totalPages: 1 });
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        page: String(page),
        pageSize: String(pagination.pageSize || 20),
        status: filter,
      });
      if (appliedQuery) params.set("q", appliedQuery);
      const response = await fetch(`/api/admin/apply-requests?${params.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load requests.");
      const rows = (data.requests || []) as ApplyRequestRow[];
      setRequests(rows);
      setStatuses((current) => ({ ...current, ...Object.fromEntries(rows.map((row) => [row.id, row.status])) }));
      setNotes((current) => ({ ...current, ...Object.fromEntries(rows.map((row) => [row.id, row.adminNote || ""])) }));
      if (data.pagination) setPagination(data.pagination as Pagination);
      if (data.statusCounts) setStatusCounts(data.statusCounts as Record<string, number>);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load requests.");
    } finally {
      setLoading(false);
    }
  }, [page, filter, appliedQuery, pagination.pageSize]);

  useEffect(() => { void load(); }, [load]);

  function search(event: React.FormEvent) {
    event.preventDefault();
    setPage(1);
    setAppliedQuery(query.trim());
  }

  function changeFilter(value: string) {
    setFilter(value);
    setPage(1);
  }

  async function save(row: ApplyRequestRow) {
    setBusy(row.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/admin/apply-requests", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: row.id, status: statuses[row.id] || row.status, note: notes[row.id] || "" }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not update request.");
      setNotice("Request updated. The status change and internal note were submitted.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update request.");
    } finally {
      setBusy("");
    }
  }

  const firstResult = pagination.totalCount === 0 ? 0 : (pagination.page - 1) * pagination.pageSize + 1;
  const lastResult = Math.min(pagination.page * pagination.pageSize, pagination.totalCount);

  return (
    <section id="documents" className="mt-12 scroll-mt-6" aria-labelledby="apply-requests-heading">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 id="apply-requests-heading" className="text-lg font-semibold text-[var(--navy)]">Apply for Me requests</h2>
          <p className="mt-1 text-sm text-gray-600">Review orders, payment state, selected universities, documents, and update request progress. Results are paginated so older requests remain accessible.</p>
        </div>
        <button type="button" onClick={() => void load()} className="btn-secondary" disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
      </div>

      <form onSubmit={search} className="mt-4 grid gap-2 sm:grid-cols-[minmax(0,1fr)_220px_auto]">
        <input className="input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search student, email, request ID, university" aria-label="Search Apply for Me requests" />
        <select className="input" value={filter} onChange={(event) => changeFilter(event.target.value)} aria-label="Filter by request status">
          <option value="all">All statuses ({Object.values(statusCounts).reduce((sum, count) => sum + count, 0)})</option>
          {STATUSES.map((status) => <option key={status} value={status}>{label(status)} ({statusCounts[status] || 0})</option>)}
        </select>
        <button type="submit" className="btn-primary" disabled={loading}>Search</button>
      </form>

      <div className="mt-3 flex flex-col gap-2 text-xs text-gray-500 sm:flex-row sm:items-center sm:justify-between">
        <p>{appliedQuery ? `Search: “${appliedQuery}” · ` : ""}{firstResult}–{lastResult} of {pagination.totalCount} matching requests</p>
        <p>Counts show requests in the current database connection; they do not confirm that requests stored elsewhere are visible.</p>
      </div>

      {error && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-4 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">{notice}</p>}
      {loading ? <p className="mt-6 text-sm text-gray-500">Loading requests…</p> : requests.length === 0 ? (
        <div className="mt-4 rounded-xl border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">
          <p>No Apply for Me requests match these filters.</p>
          <p className="mt-2">Total requests visible to this database connection: {Object.values(statusCounts).reduce((sum, count) => sum + count, 0)}.</p>
          {filter !== "all" || appliedQuery ? <button type="button" className="btn-secondary mt-3" onClick={() => { setQuery(""); setAppliedQuery(""); changeFilter("all"); }}>Clear filters</button> : <p className="mt-2">If you expected a request here, compare this count with the student account that created it. A zero count here does not prove that no request exists in another storage instance.</p>}
        </div>
      ) : (
        <>
          <div className="mt-4 space-y-4">
            {requests.map((row) => (
              <article key={row.id} className="overflow-hidden rounded-xl border border-gray-200 bg-white">
                <div className="flex flex-col gap-3 border-b border-gray-100 p-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <p className="font-semibold text-[var(--navy)]">{row.student.name || "Unnamed student"}</p>
                    <p className="text-sm text-gray-600">{row.student.email}</p>
                    <p className="mt-1 break-all text-xs text-gray-400">Request {row.id} · Updated {new Date(row.updatedAt).toLocaleString()}</p>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full bg-slate-100 px-3 py-1.5 text-slate-700">{label(row.status)}</span>
                    <span className={"rounded-full px-3 py-1.5 " + (row.paymentStatus === "paid" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800")}>{label(row.paymentStatus)}</span>
                    <span className="rounded-full bg-indigo-50 px-3 py-1.5 text-indigo-800">{money(row.feeCents)}</span>
                  </div>
                </div>
                <div className="grid gap-5 p-4 lg:grid-cols-2">
                  <div>
                    <h3 className="text-sm font-semibold text-[var(--navy)]">Selected universities ({row.items.length})</h3>
                    {row.items.length ? <ul className="mt-2 space-y-2 text-sm">{row.items.map((item) => (
                      <li key={item.id} className="rounded-lg bg-gray-50 p-3">
                        <p className="font-medium">{item.university.name}</p>
                        <p className="text-xs text-gray-500">{[item.university.city, item.university.country].filter(Boolean).join(", ")} · {label(item.status)}</p>
                        {item.preparationNote && <p className="mt-1 text-xs text-gray-600">{item.preparationNote}</p>}
                      </li>
                    ))}</ul> : <p className="mt-2 text-sm text-gray-500">No universities attached.</p>}
                    {row.studentNote && <div className="mt-3 rounded-lg border border-gray-100 p-3"><p className="text-xs font-semibold uppercase text-gray-500">Student note</p><p className="mt-1 whitespace-pre-wrap text-sm">{row.studentNote}</p></div>}
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-[var(--navy)]">Documents ({row.documents.length})</h3>
                    {row.documents.length ? <ul className="mt-2 space-y-2">{row.documents.map((doc) => (
                      <li key={doc.id} className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 p-3">
                        <div className="min-w-0"><p className="truncate text-sm font-medium">{doc.name}</p><p className="text-xs text-gray-500">{label(doc.kind)} · {(doc.size / 1024).toFixed(0)} KB · {new Date(doc.createdAt).toLocaleDateString()}</p></div>
                        <a className="shrink-0 text-sm font-medium text-[var(--teal)] hover:underline" href={`/api/admin/apply-requests/${encodeURIComponent(row.id)}/documents/${encodeURIComponent(doc.id)}`}>Download</a>
                      </li>
                    ))}</ul> : <p className="mt-2 text-sm text-gray-500">No documents uploaded.</p>}
                    <div className="mt-4">
                      <label className="text-xs font-semibold uppercase tracking-wide text-gray-500" htmlFor={`status-${row.id}`}>Request status</label>
                      <select id={`status-${row.id}`} className="input mt-1 w-full" value={statuses[row.id] || row.status} onChange={(event) => setStatuses((current) => ({ ...current, [row.id]: event.target.value }))}>
                        {STATUSES.map((status) => <option key={status} value={status}>{label(status)}</option>)}
                      </select>
                      <label className="mt-3 block text-xs font-semibold uppercase tracking-wide text-gray-500" htmlFor={`note-${row.id}`}>Internal admin note</label>
                      <textarea id={`note-${row.id}`} className="input mt-1 min-h-20 w-full" maxLength={2000} value={notes[row.id] || ""} onChange={(event) => setNotes((current) => ({ ...current, [row.id]: event.target.value }))} placeholder="Add an internal note for the audit history" />
                      <button type="button" className="btn-primary mt-3 w-full sm:w-auto" disabled={busy === row.id} onClick={() => void save(row)}>{busy === row.id ? "Saving…" : "Save changes"}</button>
                    </div>
                  </div>
                </div>
              </article>
            ))}
          </div>
          <div className="mt-5 flex flex-col gap-3 border-t border-gray-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-gray-600">Page {pagination.page} of {pagination.totalPages}</p>
            <div className="flex gap-2">
              <button type="button" className="btn-secondary" disabled={loading || page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button>
              <button type="button" className="btn-secondary" disabled={loading || page >= pagination.totalPages} onClick={() => setPage((current) => Math.min(pagination.totalPages, current + 1))}>Next</button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
