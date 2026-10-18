import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { Prisma } from "@prisma/client";
import AdminReview from "@/components/AdminReview";
import AdminAcademicRecords from "@/components/AdminAcademicRecords";
import AdminApplyRequests from "@/components/AdminApplyRequests";
import AdminUserManagement from "@/components/AdminUserManagement";
import AdminPayments from "@/components/AdminPayments";
import AdminAnalytics from "@/components/AdminAnalytics";
import AdminAuditLog from "@/components/AdminAuditLog";
import { hasProAccess } from "@/lib/billing/access";

export const dynamic = "force-dynamic";

type RangeKey = "7d" | "30d" | "all";
type ChangeLogRow = {
  id: string;
  changeType: string;
  entityType: string;
  sourceUrl: string | null;
};

const PAGE_SIZE = 20;

async function loadRecentChanges(): Promise<{
  changes: ChangeLogRow[];
  changeLogUnavailable: boolean;
}> {
  try {
    const changes = await prisma.changeLog.findMany({
      orderBy: { detectedAt: "desc" },
      take: 8,
      select: { id: true, changeType: true, entityType: true, sourceUrl: true },
    });
    return { changes, changeLogUnavailable: false };
  } catch (error) {
    if (
      error instanceof Prisma.PrismaClientKnownRequestError &&
      error.code === "P2021"
    ) {
      console.error(
        "[admin] ChangeLog table missing (P2021). Apply the pending Prisma migration."
      );
      return { changes: [], changeLogUnavailable: true };
    }
    throw error;
  }
}

function adminEmails() {
  return (process.env.ADMIN_EMAILS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
}

function isConfiguredAdmin(email?: string | null) {
  if (!email) return false;
  return adminEmails().includes(email.toLowerCase());
}

async function requireAdminStudent() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email || "";
  if (!email) return null;
  const student = email
    ? await prisma.student.findUnique({ where: { email }, select: { id: true, email: true, role: true, name: true } })
    : null;
  const allow =
    student?.role === "admin" || isConfiguredAdmin(student?.email || email);
  return allow ? student : null;
}

function normalizeRange(value?: string): RangeKey {
  if (value === "7d") return "7d";
  if (value === "all") return "all";
  return "30d";
}

function getRangeStart(range: RangeKey) {
  if (range === "7d") return new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
  if (range === "30d") return new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
  return null;
}

function formatCurrencyCents(value: number | null) {
  const safe = value ?? 0;
  return `$${(safe / 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function isSubscriptionCurrentlyActive(subscription: {
  status?: string | null;
  cancelAtPeriodEnd?: boolean | null;
  currentPeriodEnd?: Date | null;
} | null) {
  if (!subscription) return false;
  return hasProAccess({
    status: subscription.status || "",
    cancelAtPeriodEnd: Boolean(subscription.cancelAtPeriodEnd),
    currentPeriodEnd: subscription.currentPeriodEnd ?? null,
  });
}

function isPaidApplyRequest(item: { paymentStatus?: string | null }) {
  return (item.paymentStatus || "").toLowerCase() === "paid";
}

export default async function AdminPage({
  searchParams,
}: {
  searchParams?: { q?: string; page?: string; range?: string };
}) {
  const admin = await requireAdminStudent();
  if (!admin) {
    return (
      <div className="page-container py-16">
        <div className="mx-auto max-w-xl rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm">
          <h1 className="section-title">Admin access required</h1>
          <p className="mt-3 text-gray-600">
            You are signed in, but this account is not authorized to manage ProFinderNow.
            Sign in with your authorized admin account to continue.
          </p>
          <a
            href="/login?callbackUrl=%2Fadmin"
            className="btn-primary mt-6 inline-flex items-center justify-center"
          >
            Sign in to Admin
          </a>
          <p className="mt-3 text-sm text-gray-500">
            Your admin access is controlled by the account role and server configuration.
          </p>
        </div>
      </div>
    );
  }

  const rangeKey = normalizeRange(searchParams?.range);
  const q = (searchParams?.q || "").trim();
  const page = Math.max(1, Number(searchParams?.page || "1"));
  const rangeStart = getRangeStart(rangeKey);

  const [
    totalUsers,
    newUsers,
    allSubscriptions,
    paidRequests,
    totalConfirmedRevenue,
    universities,
    programs,
    fields,
    majors,
    professors,
    verifiedProfessors,
    pendingPrograms,
    unverifiedPrograms,
    missingEmail,
    missingProfessorSource,
    programsMissingSource,
    scholarshipsMissingUrl,
    universitiesMissingUrl,
    linkedFields,
    linkedDisciplines,
    linkedMajors,
    nameGroups,
    changeLogResult,
  ] = await Promise.all([
    prisma.student.count(),
    prisma.student.count({ where: rangeStart ? { createdAt: { gte: rangeStart } } : undefined }),
    prisma.subscription.findMany({
      select: { studentId: true, status: true, cancelAtPeriodEnd: true, currentPeriodEnd: true },
    }),
    prisma.applyRequest.findMany({
      where: { paymentStatus: "paid" },
      select: { studentId: true, feeCents: true },
    }),
    prisma.applyRequest.aggregate({
      _sum: { feeCents: true },
      where: { paymentStatus: "paid" },
    }),
    prisma.university.count(),
    prisma.program.count(),
    prisma.academicField.count(),
    prisma.major.count(),
    prisma.professor.count(),
    prisma.professor.count({ where: { dataStatus: "verified" } }),
    prisma.program.count({ where: { verificationStatus: "PENDING_REVIEW" } }),
    prisma.program.count({ where: { verificationStatus: "UNVERIFIED" } }),
    prisma.professor.count({ where: { OR: [{ email: null }, { email: "" }] } }),
    prisma.professor.count({
      where: { OR: [{ dataSource: null }, { dataSource: "" }] },
    }),
    prisma.program.count({
      where: { OR: [{ sourceUrl: null }, { sourceUrl: "" }] },
    }),
    prisma.scholarship.count({
      where: { OR: [{ officialUrl: null }, { officialUrl: "" }] },
    }),
    prisma.university.count({
      where: { OR: [{ officialUrl: null }, { officialUrl: "" }] },
    }),
    prisma.professorAcademicField.count(),
    prisma.professorDiscipline.count(),
    prisma.professorMajor.count(),
    prisma.professor.groupBy({ by: ["name"], _count: { _all: true } }),
    loadRecentChanges(),
  ]);

  const activeSubscriptions = allSubscriptions.filter((row) =>
    isSubscriptionCurrentlyActive(row)
  );

  const activeSubscriptionIds = new Set(activeSubscriptions.map((row) => row.studentId));
  const paidCustomerIds = new Set(
    paidRequests.filter((row) => isPaidApplyRequest(row)).map((row) => row.studentId)
  );
  const distinctPayingCustomers = new Set([
    ...activeSubscriptionIds,
    ...paidCustomerIds,
  ]).size;

  const confirmedRevenue = totalConfirmedRevenue._sum?.feeCents ?? 0;
  const confirmedOneTimePurchases = paidRequests.length;

  const { changes, changeLogUnavailable } = changeLogResult;
  const duplicateNames = nameGroups.filter((row) => row._count._all > 1).length;

  const stats = [
    ["Users", totalUsers],
    ["New users", newUsers],
    ["Paying customers", distinctPayingCustomers],
    ["Active subscriptions", activeSubscriptions.length],
    ["One-time purchases", confirmedOneTimePurchases],
    ["Confirmed revenue", formatCurrencyCents(confirmedRevenue)],
  ];

  const quality = [
    ["Universities", universities],
    ["Programs", programs],
    ["Academic fields", fields],
    ["Majors", majors],
    ["Professors", professors],
    ["Verified professors", verifiedProfessors],
    ["Pending programs", pendingPrograms],
    ["Unverified programs", unverifiedPrograms],
    ["Professors without a public email", missingEmail],
    ["Professors without a stored source", missingProfessorSource],
    ["Programs without a source URL", programsMissingSource],
    ["Scholarships without an official URL", scholarshipsMissingUrl],
    ["Universities without an official URL", universitiesMissingUrl],
    ["Professor names that repeat", duplicateNames],
    ["Stored professor-field links", linkedFields],
    ["Stored professor-discipline links", linkedDisciplines],
    ["Stored professor-major links", linkedMajors],
  ];

  const whereClause = q
    ? {
        OR: [
          { email: { contains: q, mode: "insensitive" } },
          { name: { contains: q, mode: "insensitive" } },
        ],
      }
    : undefined;

  const [customerCount, customers] = await Promise.all([
    prisma.student.count({ where: whereClause ?? undefined }),
    prisma.student.findMany({
      where: whereClause,
      orderBy: { createdAt: "desc" },
      skip: (page - 1) * PAGE_SIZE,
      take: PAGE_SIZE,
      include: {
        subscription: true,
        applyRequests: {
          where: { paymentStatus: "paid" },
          select: { id: true, feeCents: true, createdAt: true },
        },
      },
    }),
  ]);

  const totalPages = Math.max(1, Math.ceil(customerCount / PAGE_SIZE));

  const userTableRows = customers.map((student) => {
    const active = isSubscriptionCurrentlyActive(student.subscription);
    const paidCount = student.applyRequests.length;
    const totalSpent = student.applyRequests.reduce((sum, request) => sum + (request.feeCents || 0), 0);
    const lastPurchase = student.applyRequests[0]?.createdAt ?? null;

    return {
      id: student.id,
      name: student.name || "Unnamed user",
      email: student.email,
      registrationDate: student.createdAt,
      role: student.role || "student",
      plan: student.plan || (active ? "pro" : "free"),
      hasSubscription: Boolean(student.subscription),
      subscriptionStatus: student.subscription?.status || "none",
      paidCount,
      totalSpent,
      lastPurchase,
    };
  });

  const rangeLabel =
    rangeKey === "7d" ? "Last 7 days" : rangeKey === "30d" ? "Last 30 days" : "All time";

  return (
    <div className="page-container py-10">
      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--teal)]">
            ProFinderNow admin
          </p>
          <h1 className="section-title mt-2">Business dashboard</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {[
            ["7d", "Last 7 days"],
            ["30d", "Last 30 days"],
            ["all", "All time"],
          ].map(([value, label]) => (
            <a
              key={value}
              href={`/admin?range=${value}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
              className={
                "rounded-full border px-3 py-1.5 text-xs font-medium " +
                (rangeKey === value
                  ? "border-[var(--navy)] bg-[var(--navy)] text-white"
                  : "border-gray-200 bg-white text-gray-700 hover:border-[var(--teal)] hover:text-[var(--teal)]")
              }
            >
              {label}
            </a>
          ))}
        </div>
      </div>

      <nav className="mb-8 flex flex-wrap gap-2 text-sm">
        {[
          ["#dashboard", "Dashboard"],
          ["#users", "Users"],
          ["#apply", "Apply for Me"],
          ["#documents", "Documents"],
          ["#payments", "Payments"],
          ["#academic", "Academic Database"],
          ["#analytics", "Analytics"],
          ["#audit", "Audit Log"],
        ].map(([href, label]) => (
          <a
            key={href}
            href={href}
            className="rounded-full border border-gray-200 bg-white px-3 py-1.5 text-gray-700 transition hover:border-[var(--teal)] hover:text-[var(--teal)]"
          >
            {label}
          </a>
        ))}
      </nav>

      <section id="dashboard" className="space-y-6">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-[var(--navy)]">Overview</h2>
            <p className="mt-1 text-sm text-gray-600">{rangeLabel}. Revenue is limited to confirmed paid Apply for Me requests in the current schema.</p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {stats.map(([label, value]) => (
            <div key={label as string} className="card p-4">
              <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
              <p className="mt-2 text-2xl font-semibold text-[var(--navy)]">{String(value)}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="users" className="mt-12">
        <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-[var(--navy)]">Users and customers</h2>
            <p className="mt-1 text-sm text-gray-600">Search by name or email and review registration, plan, and purchase activity.</p>
          </div>
          <form method="get" className="flex flex-wrap gap-2">
            <input
              type="text"
              name="q"
              defaultValue={q}
              placeholder="Search users"
              className="input min-w-[220px]"
            />
            <input type="hidden" name="range" value={rangeKey} />
            <button type="submit" className="btn-primary">
              Search
            </button>
            {q && (
              <a href={`/admin?range=${rangeKey}`} className="btn-secondary">
                Reset
              </a>
            )}
          </form>
        </div>

        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Plan</th>
                  <th className="px-4 py-3">Subscription</th>
                  <th className="px-4 py-3">Paid requests</th>
                  <th className="px-4 py-3">Revenue</th>
                  <th className="px-4 py-3">Registered</th>
                </tr>
              </thead>
              <tbody>
                {userTableRows.map((row) => (
                  <tr key={row.id} className="border-t border-gray-200 align-top">
                    <td className="px-4 py-3">
                      <div className="font-medium text-[var(--navy)]">{row.name}</div>
                      <div className="text-xs text-gray-500">{row.email}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700">
                        {row.role}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-700">{row.plan}</td>
                    <td className="px-4 py-3 text-gray-700">
                      {row.hasSubscription ? row.subscriptionStatus : "none"}
                    </td>
                    <td className="px-4 py-3 text-gray-700">{row.paidCount}</td>
                    <td className="px-4 py-3 text-gray-700">{formatCurrencyCents(row.totalSpent)}</td>
                    <td className="px-4 py-3 text-gray-700">
                      {row.registrationDate.toLocaleDateString(undefined, {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {userTableRows.length === 0 && (
          <div className="mt-4 rounded-lg border border-dashed border-gray-200 bg-white p-6 text-sm text-gray-500">
            No users found for this search.
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-gray-600">
          <p>
            Page {page} of {totalPages} • {customerCount} total matching users
          </p>
          <div className="flex gap-2">
            <a
              href={`/admin?q=${encodeURIComponent(q)}&range=${rangeKey}&page=${Math.max(1, page - 1)}`}
              aria-disabled={page <= 1}
              className={
                "rounded border px-3 py-1.5 " +
                (page <= 1 ? "cursor-not-allowed border-gray-200 text-gray-400" : "border-gray-200 text-gray-700 hover:border-[var(--teal)] hover:text-[var(--teal)]")
              }
            >
              Previous
            </a>
            <a
              href={`/admin?q=${encodeURIComponent(q)}&range=${rangeKey}&page=${Math.min(totalPages, page + 1)}`}
              aria-disabled={page >= totalPages}
              className={
                "rounded border px-3 py-1.5 " +
                (page >= totalPages ? "cursor-not-allowed border-gray-200 text-gray-400" : "border-gray-200 text-gray-700 hover:border-[var(--teal)] hover:text-[var(--teal)]")
              }
            >
              Next
            </a>
          </div>
        </div>
        <AdminUserManagement />
      </section>

      <div id="apply">
        <AdminApplyRequests />
      </div>

      <AdminPayments />

      <section id="academic" className="mt-12">
        <h2 className="text-lg font-semibold text-[var(--navy)]">Academic database</h2>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          Counts come from the live database. Official imports are marked from their source file. Seeded technology programs stay unverified until an official page is checked.
        </p>
        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {quality.map(([label, value]) => (
            <div key={label as string} className="card p-4">
              <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
              <p className="mt-1 text-2xl font-semibold text-[var(--navy)]">{String(value)}</p>
            </div>
          ))}
        </div>

        <h2 className="mt-10 text-lg font-semibold text-[var(--navy)]">Recent source changes</h2>
        {changeLogUnavailable && (
          <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            The audit log table (<code>ChangeLog</code>) is not present in the production database yet. Dashboard counts still work. Apply the pending Prisma migration that creates <code>ChangeLog</code>, then refresh this page.
          </div>
        )}
        <ul className="mt-3 space-y-2 text-sm">
          {changes.map((row) => (
            <li key={row.id} className="card p-3">
              <span className="font-medium">{row.changeType}</span>
              <span className="ml-2 text-gray-500">{row.entityType}</span>
              {row.sourceUrl && (
                <a className="ml-2 text-[var(--teal)]" href={row.sourceUrl}>
                  source
                </a>
              )}
            </li>
          ))}
          {changes.length === 0 && !changeLogUnavailable && (
            <li className="text-gray-500">No source changes recorded yet. Run the academic sync.</li>
          )}
        </ul>

        <AdminReview />
        <AdminAcademicRecords />
      </section>

      <AdminAnalytics />
      <AdminAuditLog />
    </div>
  );
}
