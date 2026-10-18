"use client";

import { useCallback, useEffect, useState } from "react";

type Tab = "requests" | "subscriptions" | "events";
type RequestRow = { id: string; studentId: string; feeCents: number; paymentStatus: string; paymentRef: string | null; package: string; status: string; createdAt: string; updatedAt: string; student: { name: string | null; email: string } };
type SubscriptionRow = { id: string; studentId: string; plan: string; status: string; provider: string; providerSubscriptionId: string | null; priceId: string | null; currentPeriodEnd: string | null; cancelAtPeriodEnd: boolean; updatedAt: string; student: { name: string | null; email: string } };
type EventRow = { id: string; type: string; createdAt: string };
type Summary = { applyRequestCount: number; paidApplyRequestCount: number; recordedPaidApplyRequestFeesCents: number; subscriptionCount: number; activeSubscriptionCount: number; billingEventCount: number };

const money = (cents: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);
const PAGE_SIZE = 20;

export default function AdminPayments() {
  const [tab, setTab] = useState<Tab>("requests");
  const [rows, setRows] = useState<Array<RequestRow | SubscriptionRow | EventRow>>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [limitation, setLimitation] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ tab, page: String(page), pageSize: String(PAGE_SIZE), q: query, status: filter });
      const response = await fetch(`/api/admin/payments?${params.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load payment records.");
      setRows(data.rows || []);
      setSummary(data.summary || null);
      setTotal(data.total || 0);
      setLimitation(data.limitation || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load payment records.");
    } finally {
      setLoading(false);
    }
  }, [tab, page, query, filter]);

  useEffect(() => { void load(); }, [load]);

  function changeTab(next: Tab) {
    setTab(next);
    setPage(1);
    setFilter("all");
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section id="payments" className="mt-12 scroll-mt-6" aria-labelledby="payments-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 id="payments-heading" className="text-lg font-semibold text-[var(--navy)]">Payments and billing records</h2><p className="mt-1 text-sm text-gray-600">Searchable, paginated records. This view is read-only and does not trigger payments or refunds.</p></div>
        <button type="button" className="btn-secondary" disabled={loading} onClick={() => void load()}>Refresh records</button>
      </div>

      {summary && <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Apply for Me requests</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{summary.applyRequestCount}</p></div>
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Requests marked paid</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{summary.paidApplyRequestCount}</p></div>
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Recorded paid request fees</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{money(summary.recordedPaidApplyRequestFeesCents)}</p></div>
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Active subscriptions</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{summary.activeSubscriptionCount} <span className="text-sm font-normal text-gray-500">/ {summary.subscriptionCount}</span></p></div>
      </div>}

      {error && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <div className="flex flex-wrap gap-2">
          {([["requests", "Apply for Me"], ["subscriptions", "Subscriptions"], ["events", "Stripe events"]] as const).map(([value, label]) => <button key={value} type="button" className={`rounded-full border px-3 py-2 text-sm ${tab === value ? "bg-[var(--navy)] text-white" : "bg-white text-gray-700"}`} onClick={() => changeTab(value)}>{label}</button>)}
        </div>
        <input className="input flex-1" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder={tab === "events" ? "Search event ID or type" : "Search customer, email, ID, or reference"} aria-label="Search payment records" />
        <select className="input sm:max-w-52" value={filter} onChange={(event) => { setFilter(event.target.value); setPage(1); }} aria-label="Filter records by status">
          <option value="all">All {tab === "events" ? "event types" : "statuses"}</option>
          {(tab === "requests" ? ["paid", "unpaid", "pending", "failed", "refunded"] : tab === "subscriptions" ? ["active", "trialing", "past_due", "canceled", "incomplete"] : ["checkout.session.completed", "invoice.payment_succeeded", "invoice.payment_failed", "customer.subscription.updated", "customer.subscription.deleted"]).map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </div>

      {loading ? <p className="mt-5 text-sm text-gray-500">Loading records…</p> : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white">
          {tab === "requests" ? <table className="min-w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-3">Customer</th><th className="px-3 py-3">Request / package</th><th className="px-3 py-3">Recorded fee</th><th className="px-3 py-3">Payment</th><th className="px-3 py-3">Reference</th><th className="px-3 py-3">Updated</th></tr></thead><tbody>{(rows as RequestRow[]).map((row) => <tr key={row.id} className="border-t border-gray-100"><td className="px-3 py-3"><p className="font-medium">{row.student.name || "Unnamed user"}</p><p className="text-xs text-gray-500">{row.student.email}</p></td><td className="px-3 py-3"><p>{row.package}</p><p className="text-xs text-gray-400">{row.id}</p></td><td className="px-3 py-3">{money(row.feeCents)}</td><td className="px-3 py-3">{row.paymentStatus}</td><td className="px-3 py-3 text-xs">{row.paymentRef || "Not recorded"}</td><td className="px-3 py-3 text-xs">{new Date(row.updatedAt).toLocaleString()}</td></tr>)}</tbody></table> :
          tab === "subscriptions" ? <table className="min-w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-3">Customer</th><th className="px-3 py-3">Plan</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Provider</th><th className="px-3 py-3">Provider reference</th><th className="px-3 py-3">Period end</th></tr></thead><tbody>{(rows as SubscriptionRow[]).map((row) => <tr key={row.id} className="border-t border-gray-100"><td className="px-3 py-3"><p className="font-medium">{row.student.name || "Unnamed user"}</p><p className="text-xs text-gray-500">{row.student.email}</p></td><td className="px-3 py-3">{row.plan}</td><td className="px-3 py-3">{row.status}{row.cancelAtPeriodEnd ? " · cancels at period end" : ""}</td><td className="px-3 py-3">{row.provider}</td><td className="px-3 py-3 text-xs">{row.providerSubscriptionId || row.priceId || "Not recorded"}</td><td className="px-3 py-3">{row.currentPeriodEnd ? new Date(row.currentPeriodEnd).toLocaleDateString() : "Not recorded"}</td></tr>)}</tbody></table> :
          <table className="min-w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-3">Stripe event ID</th><th className="px-3 py-3">Event type</th><th className="px-3 py-3">Recorded at</th></tr></thead><tbody>{(rows as EventRow[]).map((row) => <tr key={row.id} className="border-t border-gray-100"><td className="px-3 py-3 font-mono text-xs">{row.id}</td><td className="px-3 py-3">{row.type}</td><td className="px-3 py-3">{new Date(row.createdAt).toLocaleString()}</td></tr>)}</tbody></table>}
          {rows.length === 0 && <p className="p-5 text-sm text-gray-500">No records match these filters.</p>}
        </div>
      )}

      {!loading && <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><p className="text-sm text-gray-500">Showing {total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total} records · Page {page} of {totalPages}</p><div className="flex gap-2"><button type="button" className="btn-secondary" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button><button type="button" className="btn-secondary" disabled={page >= totalPages || loading} onClick={() => setPage((value) => Math.min(totalPages, value + 1))}>Next</button></div></div>}
      {limitation && <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{limitation}</p>}
    </section>
  );
}
