"use client";

import { useCallback, useEffect, useState } from "react";

type Entity = "universities" | "programs" | "scholarships";
type Row = {
  id: string;
  name?: string;
  major?: string;
  officialName?: string | null;
  degree?: string;
  type?: string | null;
  city?: string | null;
  country?: string | null;
  universityName?: string;
  officialUrl?: string | null;
  sourceUrl?: string | null;
  programUrl?: string | null;
  verificationStatus: string;
  dataStatus?: string;
  updatedAt?: string;
};
type Draft = { verificationStatus: string; officialUrl: string; sourceUrl: string; programUrl: string };
type PageInfo = { page: number; pageSize: number; totalCount: number; totalPages: number };

const LABELS: Record<Entity, string> = { universities: "Universities", programs: "Programs", scholarships: "Scholarships" };
const STATUSES = ["UNVERIFIED", "PENDING_REVIEW", "VERIFIED", "NEEDS_REVIEW", "OUTDATED"];

export default function AdminAcademicRecords() {
  const [entity, setEntity] = useState<Entity>("universities");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<Row[]>([]);
  const [pagination, setPagination] = useState<PageInfo>({ page: 1, pageSize: 20, totalCount: 0, totalPages: 1 });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ entity, page: String(page), pageSize: "20" });
      if (search) params.set("q", search);
      const response = await fetch(`/api/admin/academic-records?${params.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to load academic records.");
      setRows(data.rows || []);
      setPagination(data.pagination);
      setDrafts((current) => {
        const next = { ...current };
        for (const row of (data.rows || []) as Row[]) {
          next[row.id] = {
            verificationStatus: row.verificationStatus || "UNVERIFIED",
            officialUrl: row.officialUrl || "",
            sourceUrl: row.sourceUrl || "",
            programUrl: row.programUrl || "",
          };
        }
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load academic records.");
    } finally {
      setLoading(false);
    }
  }, [entity, page, search]);

  useEffect(() => { void load(); }, [load]);

  function updateDraft(id: string, patch: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  async function save(row: Row) {
    const draft = drafts[row.id];
    if (!draft) return;
    setSaving(row.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/admin/academic-records", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity, id: row.id, ...draft }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to save this record.");
      setNotice("Record saved. Verification changes are recorded in the audit log when available.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to save this record.");
    } finally {
      setSaving("");
    }
  }

  return (
    <section className="mt-10 rounded-2xl border border-gray-200 bg-white p-4 sm:p-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 className="text-lg font-semibold text-[var(--navy)]">Academic record manager</h3>
          <p className="mt-1 max-w-2xl text-sm text-gray-600">Search and review university, program, and scholarship records. Update source links and verification status without deleting records.</p>
        </div>
        <span className="text-sm text-gray-500">{pagination.totalCount.toLocaleString()} records</span>
      </div>

      <div className="mt-5 flex flex-wrap gap-2" role="tablist" aria-label="Academic record type">
        {(Object.keys(LABELS) as Entity[]).map((value) => (
          <button key={value} type="button" role="tab" aria-selected={entity === value}
            onClick={() => { setEntity(value); setPage(1); setSearch(""); setQuery(""); setNotice(""); }}
            className={"rounded-full border px-3 py-2 text-sm " + (entity === value ? "border-[var(--teal)] bg-teal-50 font-semibold text-[var(--navy)]" : "border-gray-200 text-gray-600 hover:border-[var(--teal)]")}>
            {LABELS[value]}
          </button>
        ))}
      </div>

      <form className="mt-4 flex flex-col gap-2 sm:flex-row" onSubmit={(event) => { event.preventDefault(); setPage(1); setSearch(query.trim()); }}>
        <input className="input min-w-0 flex-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${LABELS[entity].toLowerCase()}...`} maxLength={120} />
        <button className="btn-primary" type="submit">Search</button>
        <button className="btn-secondary" type="button" onClick={() => { setQuery(""); setSearch(""); setPage(1); }}>Reset</button>
      </form>

      {error && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-4 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">{notice}</p>}
      {loading && <p className="mt-4 text-sm text-gray-500">Loading records…</p>}
      {!loading && rows.length === 0 && <p className="mt-4 rounded-lg border border-dashed border-gray-200 p-6 text-sm text-gray-500">No records found.</p>}

      <div className="mt-4 space-y-4">
        {rows.map((row) => {
          const draft = drafts[row.id] || { verificationStatus: row.verificationStatus || "UNVERIFIED", officialUrl: row.officialUrl || "", sourceUrl: row.sourceUrl || "", programUrl: row.programUrl || "" };
          const title = row.name || row.officialName || row.major || "Untitled record";
          const subtitle = entity === "universities" ? [row.city, row.country].filter(Boolean).join(", ") : entity === "programs" ? [row.degree, row.universityName].filter(Boolean).join(" · ") : [row.type, row.universityName].filter(Boolean).join(" · ");
          return (
            <article key={row.id} className="rounded-xl border border-gray-200 p-4">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <h4 className="break-words font-semibold text-[var(--navy)]">{title}</h4>
                  {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
                  <p className="mt-1 break-all text-xs text-gray-400">ID: {row.id}</p>
                </div>
                <span className="mt-1 inline-flex w-fit rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">{row.verificationStatus || "UNVERIFIED"}</span>
              </div>

              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <label className="text-sm text-gray-700">Verification status
                  <select className="input mt-1 w-full" value={draft.verificationStatus} onChange={(event) => updateDraft(row.id, { verificationStatus: event.target.value })}>
                    {STATUSES.map((status) => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}
                  </select>
                </label>
                {entity !== "programs" ? (
                  <label className="text-sm text-gray-700">Official URL
                    <input className="input mt-1 w-full" type="url" placeholder="https://..." value={draft.officialUrl} onChange={(event) => updateDraft(row.id, { officialUrl: event.target.value })} maxLength={2000} />
                  </label>
                ) : (
                  <label className="text-sm text-gray-700">Program URL
                    <input className="input mt-1 w-full" type="url" placeholder="https://..." value={draft.programUrl} onChange={(event) => updateDraft(row.id, { programUrl: event.target.value })} maxLength={2000} />
                  </label>
                )}
                {(entity === "universities" || entity === "programs") && (
                  <label className="text-sm text-gray-700 md:col-span-2">Source URL
                    <input className="input mt-1 w-full" type="url" placeholder="https://official-source..." value={draft.sourceUrl} onChange={(event) => updateDraft(row.id, { sourceUrl: event.target.value })} maxLength={2000} />
                  </label>
                )}
              </div>
              <div className="mt-4 flex justify-end">
                <button type="button" className="btn-primary" disabled={saving === row.id} onClick={() => void save(row)}>{saving === row.id ? "Saving…" : "Save record"}</button>
              </div>
            </article>
          );
        })}
      </div>

      <div className="mt-5 flex items-center justify-between gap-3 border-t border-gray-100 pt-4 text-sm text-gray-600">
        <p>Page {pagination.page} of {pagination.totalPages}</p>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" disabled={loading || page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button>
          <button type="button" className="btn-secondary" disabled={loading || page >= pagination.totalPages} onClick={() => setPage((value) => Math.min(pagination.totalPages, value + 1))}>Next</button>
        </div>
      </div>
    </section>
  );
}
