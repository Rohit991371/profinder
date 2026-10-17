"use client";

import { useCallback, useEffect, useState } from "react";

type Metrics = { totalUsers: number; newUsers: number; totalRequests: number; newRequests: number; paidRequests: number; unpaidRequests: number; confirmedApplyRevenueCents: number; totalSubscriptions: number; activeSubscriptions: number; universities: number; programs: number; professors: number; verifiedProfessors: number; scholarships: number };
const money = (cents: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);

export default function AdminAnalytics() {
  const [range, setRange] = useState("30d");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/admin/analytics?range=${range}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load analytics.");
      setMetrics(data.metrics);
      setNote(data.revenueNote || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load analytics.");
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => { void load(); }, [load]);
  const cards = metrics ? [
    ["Total users", metrics.totalUsers.toLocaleString()],
    ["New users", metrics.newUsers.toLocaleString()],
    ["Apply for Me requests", metrics.totalRequests.toLocaleString()],
    ["New requests", metrics.newRequests.toLocaleString()],
    ["Paid requests", metrics.paidRequests.toLocaleString()],
    ["Unpaid / other requests", metrics.unpaidRequests.toLocaleString()],
    ["Confirmed request fees", money(metrics.confirmedApplyRevenueCents)],
    ["Active subscriptions", metrics.activeSubscriptions.toLocaleString()],
    ["All subscriptions", metrics.totalSubscriptions.toLocaleString()],
    ["Universities", metrics.universities.toLocaleString()],
    ["Programs", metrics.programs.toLocaleString()],
    ["Professors", metrics.professors.toLocaleString()],
    ["Verified professors", metrics.verifiedProfessors.toLocaleString()],
    ["Scholarships", metrics.scholarships.toLocaleString()],
  ] : [];

  return (
    <section id="analytics" className="mt-12 scroll-mt-6" aria-labelledby="analytics-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><h2 id="analytics-heading" className="text-lg font-semibold text-[var(--navy)]">Analytics</h2><p className="mt-1 text-sm text-gray-600">Live counts queried from the database; period metrics use record creation/update dates.</p></div><select className="input sm:max-w-48" value={range} onChange={(event) => setRange(event.target.value)} aria-label="Analytics date range"><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option><option value="all">All time</option></select></div>
      {error && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {loading ? <p className="mt-5 text-sm text-gray-500">Loading analytics…</p> : <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{cards.map(([label, value]) => <div key={label} className="card p-4"><p className="text-xs uppercase tracking-wide text-gray-500">{label}</p><p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{value}</p></div>)}</div>}
      {note && <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{note}</p>}
    </section>
  );
}
