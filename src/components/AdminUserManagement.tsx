"use client";

import { useCallback, useEffect, useState } from "react";

type UserRow = { id: string; name: string | null; email: string; role: string; plan: string; createdAt: string; subscription: { status: string } | null };

export default function AdminUserManagement() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/admin/users", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load users.");
      const rows = data.users as UserRow[];
      setUsers(rows);
      setRoles(Object.fromEntries(rows.map((row) => [row.id, row.role || "student"])));
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

  const visible = users.filter((user) => [user.name || "", user.email, user.role, user.plan].some((value) => value.toLowerCase().includes(query.trim().toLowerCase())));

  return (
    <section className="mt-6 rounded-xl border border-gray-200 bg-white p-4" aria-labelledby="user-management-heading">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 id="user-management-heading" className="font-semibold text-[var(--navy)]">Account access management</h3>
          <p className="mt-1 text-sm text-gray-600">Review the latest 100 accounts and change student/admin roles. Passwords and private credentials are never shown.</p>
        </div>
        <button className="btn-secondary" type="button" disabled={loading} onClick={() => void load()}>Refresh</button>
      </div>
      <input className="input mt-4 w-full" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter accounts by name, email, role, or plan" aria-label="Filter accounts" />
      {error && <p role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-3 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">{notice}</p>}
      {loading ? <p className="mt-4 text-sm text-gray-500">Loading accounts…</p> : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500"><tr><th className="px-3 py-2">Account</th><th className="px-3 py-2">Plan / subscription</th><th className="px-3 py-2">Role</th><th className="px-3 py-2">Action</th></tr></thead>
            <tbody>{visible.map((user) => (
              <tr key={user.id} className="border-t border-gray-100">
                <td className="px-3 py-3"><p className="font-medium text-[var(--navy)]">{user.name || "Unnamed user"}</p><p className="text-xs text-gray-500">{user.email}</p><p className="text-xs text-gray-400">Joined {new Date(user.createdAt).toLocaleDateString()}</p></td>
                <td className="px-3 py-3 text-gray-600">{user.plan || "free"} · {user.subscription?.status || "no subscription"}</td>
                <td className="px-3 py-3"><select className="input min-w-28" aria-label={`Role for ${user.email}`} value={roles[user.id] || user.role} onChange={(event) => setRoles((current) => ({ ...current, [user.id]: event.target.value }))}><option value="student">Student</option><option value="admin">Admin</option></select></td>
                <td className="px-3 py-3"><button type="button" className="btn-secondary whitespace-nowrap" disabled={busy === user.id || (roles[user.id] || user.role) === user.role} onClick={() => void saveRole(user)}>{busy === user.id ? "Saving…" : "Save role"}</button></td>
              </tr>
            ))}</tbody>
          </table>
          {visible.length === 0 && <p className="p-4 text-sm text-gray-500">No accounts match that filter.</p>}
        </div>
      )}
      <p className="mt-3 text-xs text-gray-500">Safety: your own admin access and accounts listed in ADMIN_EMAILS cannot be demoted here. Role changes are recorded in the audit log.</p>
    </section>
  );
}
