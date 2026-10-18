"use client";

import { useCallback, useEffect, useState } from "react";

type UserRow = { id: string; name: string | null; email: string; role: string; plan: string; createdAt: string; subscription: { status: string } | null };
type Pagination = { page: number; pageSize: number; totalCount: number; totalPages: number };

export default function AdminUserManagement() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState<Pagination>({ page: 1, pageSize: 20, totalCount: 0, totalPages: 1 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: String(page), pageSize: String(pagination.pageSize || 20) });
      if (appliedQuery) params.set("q", appliedQuery);
      const response = await fetch(`/api/admin/users?${params.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load users.");
      const rows = data.users as UserRow[];
      setUsers(rows);
      setRoles((current) => ({ ...current, ...Object.fromEntries(rows.map((row) => [row.id, row.role || "student"])) }));
      if (data.pagination) setPagination(data.pagination as Pagination);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load users.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function saveRole(user: UserRow) {
    setBusy(user.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/admin/users", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: user.id, role: roles[user.id] || user.role }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not update role.");
      setNotice(`Role updated for ${user.email}.`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update role.");
    } finally {
      setBusy("");
    }
  }

  function search(event: React.FormEvent) {
    event.preventDefault();
    setPage(1);
    setAppliedQuery(query.trim());
  }

  const firstResult = pagination.totalCount === 0 ? 0 : (pagination.page - 1) * pagination.pageSize + 1;
  const lastResult = Math.min(pagination.page * pagination.pageSize, pagination.totalCount);

  return (
    <section className="mt-6 rounded-xl border border-gray-200 bg-white p-4" aria-labelledby="user-management-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 id="user-management-heading" className="font-semibold text-[var(--navy)]">Account access management</h3>
          <p className="mt-1 text-sm text-gray-600">Search and paginate all accounts, then change student/admin roles. Passwords and private credentials are never shown.</p>
        </div>
        <button className="btn-secondary" type="button" disabled={loading} onClick={() => void load()}>Refresh</button>
      </div>
      <form className="mt-4 flex flex-col gap-2 sm:flex-row" onSubmit={search}>
        <input className="input w-full flex-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search accounts by name, email, role, or plan" aria-label="Search accounts" />
        <button type="submit" className="btn-primary" disabled={loading}>Search</button>
      </form>
      <p className="mt-2 text-xs text-gray-500">{firstResult}–{lastResult} of {pagination.totalCount} matching accounts</p>
      {error && <p role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-3 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">{notice}</p>}
      {loading ? <p className="mt-4 text-sm text-gray-500">Loading accounts…</p> : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-2">Account</th><th className="px-3 py-2">Plan / subscription</th><th className="px-3 py-2">Role</th><th className="px-3 py-2">Action</th></tr></thead>
            <tbody>{users.map((user) => (
              <tr key={user.id} className="border-t border-gray-100">
                <td className="px-3 py-3"><p className="font-medium text-[var(--navy)]">{user.name || "Unnamed user"}</p><p className="text-xs text-gray-500">{user.email}</p><p className="text-xs text-gray-400">Joined {new Date(user.createdAt).toLocaleDateString()}</p></td>
                <td className="px-3 py-3 text-gray-600">{user.plan || "free"} · {user.subscription?.status || "no subscription"}</td>
                <td className="px-3 py-3"><select className="input min-w-28" aria-label={`Role for ${user.email}`} value={roles[user.id] || user.role} onChange={(event) => setRoles((current) => ({ ...current, [user.id]: event.target.value }))}><option value="student">Student</option><option value="admin">Admin</option></select></td>
                <td className="px-3 py-3"><button type="button" className="btn-secondary whitespace-nowrap" disabled={busy === user.id || (roles[user.id] || user.role) === user.role} onClick={() => void saveRole(user)}>{busy === user.id ? "Saving…" : "Save role"}</button></td>
              </tr>
            ))}</tbody>
          </table>
          {users.length === 0 && <p className="p-4 text-sm text-gray-500">No accounts match this search.</p>}
        </div>
      )}
      {!loading && <div className="mt-4 flex items-center justify-between gap-3">
        <p className="text-xs text-gray-500">Page {pagination.page} of {pagination.totalPages}</p>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" disabled={loading || page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button>
          <button type="button" className="btn-secondary" disabled={loading || page >= pagination.totalPages} onClick={() => setPage((current) => Math.min(pagination.totalPages, current + 1))}>Next</button>
        </div>
      </div>}
      <p className="mt-3 text-xs text-gray-500">Safety: your own admin access and accounts listed in ADMIN_EMAILS cannot be demoted here. Role changes are recorded in the audit log.</p>
    </section>
  );
}
