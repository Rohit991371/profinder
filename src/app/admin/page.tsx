import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import AdminReview from "@/components/AdminReview";
import { Prisma } from "@prisma/client";

export const dynamic = "force-dynamic";

type ChangeLogRow = {
  id: string;
  changeType: string;
  entityType: string;
  sourceUrl: string | null;
};

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
    // P2021 = table does not exist. Keep the rest of the admin dashboard usable.
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

export default async function AdminPage() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email || "";
  const student = email
    ? await prisma.student.findUnique({ where: { email }, select: { role: true } })
    : null;
  const allow =
    student?.role === "admin" ||
    (process.env.ADMIN_EMAILS || "")
      .split(",")
      .map((v) => v.trim().toLowerCase())
      .filter(Boolean)
      .includes(email.toLowerCase());
  if (!allow) {
    return (
      <div className="page-container py-16">
        <h1 className="section-title">Admin only</h1>
        <p className="mt-2 text-gray-600">This account cannot view data operations.</p>
      </div>
    );
  }

  const [
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

  const { changes, changeLogUnavailable } = changeLogResult;
  const duplicateNames = nameGroups.filter((row) => row._count._all > 1).length;
  const stats = [
    ["Universities", universities],
    ["Programs", programs],
    ["Academic fields", fields],
    ["Majors", majors],
    ["Professors", professors],
    ["Verified professors", verifiedProfessors],
    ["Pending programs", pendingPrograms],
    ["Unverified programs", unverifiedPrograms],
  ];
  const quality = [
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

  return (
    <div className="page-container py-10">
      <h1 className="section-title">Admin</h1>
      <p className="mt-1 max-w-2xl text-sm text-gray-600">
        Counts come from the live database. Official imports are marked from their source
        file. Seeded technology programs stay unverified until an official page is checked.
      </p>
      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map(([label, value]) => (
          <div key={label as string} className="card p-4">
            <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--navy)]">{value}</p>
          </div>
        ))}
      </div>
      <h2 className="mt-10 text-lg font-semibold text-[var(--navy)]">Data quality</h2>
      <p className="mt-1 max-w-2xl text-sm text-gray-600">
        These are missing fields and stored links. A missing field is not treated as a fact,
        and a repeated name is not automatically merged. Zero academic links means no
        professor was connected to that taxonomy. The app does not guess a major from a
        department name.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {quality.map(([label, value]) => (
          <div key={label as string} className="card p-4">
            <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
            <p className="mt-1 text-2xl font-semibold text-[var(--navy)]">{value}</p>
          </div>
        ))}
      </div>
      <h2 className="mt-10 text-lg font-semibold text-[var(--navy)]">Recent source changes</h2>
      {changeLogUnavailable && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          The audit log table (<code>ChangeLog</code>) is not present in the production
          database yet. Dashboard counts still work. Apply the pending Prisma migration
          that creates <code>ChangeLog</code>, then refresh this page.
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
    </div>
  );
}
