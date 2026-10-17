"use client";

import { useCallback, useEffect, useState } from "react";

type PaymentRequest = { id: string; studentId: string; feeCents: number; paymentStatus: string; paymentRef: string | null; package: string; status: string; createdAt: string; updatedAt: string; student: { name: string | null; email: string }; recordType: string };
type SubscriptionRow = { id: string; studentId: string; plan: string; status: string; provider: string; providerSubscriptionId: string | null; priceId: string | null; currentPeriodEnd: string | null; cancelAtPeriodEnd: boolean; updatedAt: string; student: { name: string | null; email: string }; recordType: string };

const money = (cents: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);

export default function AdminPayments() {
  const [requests, setRequests] = useState<PaymentRequest[]>([]);
  const [subscriptions, setSubscriptions] = useState<SubscriptionRow[]>([]);
  const [tab, setTab] = useState("requests");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [limitation, setLimitation] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/admin/payments", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load payment records.");
      setRequests(data.requests || []);
      setSubscriptions(data.subscriptions || []);
      setLimitation(data.limitation || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load payment records.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const term = query.trim().toLowerCase();
  const visibleRequests = requests.filter((row) => (filter === "all" || row.paymentStatus === filter) && [row.student.name || "", row.student.email, row.id, row.paymentRef || ""].some((value) => value.toLowerCase().includes(term)));
  const visibleSubscriptions = subscriptions.filter((row) => (filter === "all" || row.status === filter) && [row.student.name || "", row.student.email, row.id, row.providerSubscriptionId || "", row.plan].some((value) => value.toLowerCase().includes(term)));
  const confirmedCents = requests.filter((row) => row.paymentStatus.toLowerCase() === "paid").reduce((sum, row) => sum + row.feeCents, 0);

  return (
    <section id="payments" className="mt-12 scroll-mt-6" aria-labelledby="payments-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 id="payments-heading" className="text-lg font-semibold text-[var(--navy)]">Payments and subscriptions</h2><p className="mt-1 text-sm text-gray-600">Read-only records from the database. No payment or refund is triggered from this screen.</p></div>
        <button type="button" className="btn-secondary" disabled={loading} onClick={() => void load()}>Refresh records</button>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Apply for Me records</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{requests.length}</p></div>
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Marked paid</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{requests.filter((row) => row.paymentStatus.toLowerCase() === "paid").length}</p></div>
        <div className="card p-4"><p className="text-xs uppercase text-gray-500">Confirmed request fees</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{money(confirmedCents)}</p></div>
      </div>
      {error && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <div className="flex gap-2"><button type="button" className={`rounded-full border px-3 py-2 text-sm ${tab === "requests" ? "bg-[var(--navy)] text-white" : "bg-white text-gray-700"}`} onClick={() => { setTab("requests"); setFilter("all"); }}>Apply for Me</button><button type="button" className={`rounded-full border px-3 py-2 text-sm ${tab === "subscriptions" ? "bg-[var(--navy)] text-white" : "bg-white text-gray-700"}`} onClick={() => { setTab("subscriptions"); setFilter("all"); }}>Subscriptions</button></div>
        <input className="input flex-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by customer, email, record ID, or reference" />
        <select className="input sm:max-w-48" value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter payment records"><option value="all">All statuses</option>{(tab === "requests" ? ["paid", "unpaid", "pending", "failed", "refunded"] : ["active", "trialing", "past_due", "canceled", "incomplete"]).map((value) => <option key={value} value={value}>{value}</option>)}</select>
      </div>
      {loading ? <p className="mt-5 text-sm text-gray-500">Loading records…</p> : tab === "requests" ? (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white"><table className="min-w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-3">Customer</th><th className="px-3 py-3">Request / package</th><th className="px-3 py-3">Amount</th><th className="px-3 py-3">Payment</th><th className="px-3 py-3">Reference</th><th className="px-3 py-3">Updated</th></tr></thead><tbody>{visibleRequests.map((row) => <tr key={row.id} className="border-t border-gray-100"><td className="px-3 py-3"><p className="font-medium">{row.student.name || "Unnamed user"}</p><p className="text-xs text-gray-500">{row.student.email}</p></td><td className="px-3 py-3"><p>{row.package}</p><p className="text-xs text-gray-400">{row.id}</p></td><td className="px-3 py-3">{money(row.feeCents)}</td><td className="px-3 py-3"><span className="rounded-full bg-gray-100 px-2 py-1 text-xs">{row.paymentStatus}</span></td><td className="px-3 py-3 text-xs">{row.paymentRef || "Not recorded"}</td><td className="px-3 py-3 text-xs">{new Date(row.updatedAt).toLocaleString()}</td></tr>)}</tbody></table>{visibleRequests.length === 0 && <p className="p-5 text-sm text-gray-500">No payment records match.</p>}</div>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white"><table className="min-w-full text-left text-sm"><thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-3">Customer</th><th className="px-3 py-3">Plan</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Provider</th><th className="px-3 py-3">Provider reference</th><th className="px-3 py-3">Period end</th></tr></thead><tbody>{visibleSubscriptions.map((row) => <tr key={row.id} className="border-t border-gray-100"><td className="px-3 py-3"><p className="font-medium">{row.student.name || "Unnamed user"}</p><p className="text-xs text-gray-500">{row.student.email}</p></td><td className="px-3 py-3">{row.plan}</td><td className="px-3 py-3">{row.status}{row.cancelAtPeriodEnd ? " · cancels at period end" : ""}</td><td className="px-3 py-3">{row.provider}</td><td className="px-3 py-3 text-xs">{row.providerSubscriptionId || row.priceId || "Not recorded"}</td><td className="px-3 py-3">{row.currentPeriodEnd ? new Date(row.currentPeriodEnd).toLocaleDateString() : "Not recorded"}</td></tr>)}</tbody></table>{visibleSubscriptions.length === 0 && <p className="p-5 text-sm text-gray-500">No subscription records match.</p>}</div>
      )}
      {limitation && <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{limitation}</p>}
    </section>
  );
}
