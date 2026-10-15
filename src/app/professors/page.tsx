import Link from "next/link";
import type { Metadata } from "next";
import { getServerSession } from "next-auth";
import { prisma, ensureSchema } from "@/lib/prisma";
import { authOptions } from "@/lib/auth";
import { computeResearchMatch } from "@/lib/matching";
import { assessTarget, newestStoredYear } from "@/lib/professor-assessment";
import ProfessorCard from "@/components/ProfessorCard";
import ProfessorFilters, { type ProfessorFilterValues } from "@/components/ProfessorFilters";
import { professorQueryWhere } from "@/lib/discovery";
import { SITE } from "@/lib/seo";

export const dynamic = "force-dynamic";

type SearchParams = {
  area?: string;
  research?: string;
  q?: string;
  page?: string;
  university?: string;
  verified?: string;
  email?: string;
  department?: string;
  papers?: string;
  funding?: string;
  field?: string;
  discipline?: string;
  major?: string;
  country?: string;
};

export async function generateMetadata({ searchParams }: { searchParams: SearchParams }): Promise<Metadata> {
  const filtered = Boolean(searchParams.q || searchParams.area || searchParams.research || searchParams.university || searchParams.verified || searchParams.email || searchParams.department || searchParams.papers || searchParams.page || searchParams.field || searchParams.discipline || searchParams.major || searchParams.country);
  return {
    title: { absolute: "Professors | ProFinder" },
    description: "Find professors who match your interests. Filters use stored links only.",
    alternates: { canonical: `${SITE}/professors` },
    robots: filtered ? { index: false, follow: true } : { index: true, follow: true },
  };
}

const PAGE_SIZE = 24;

function pageHref(page: number, filters: Omit<SearchParams, "page">) {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.area) params.set("area", filters.area);
  if (filters.research) params.set("research", filters.research);
  if (filters.university) params.set("university", filters.university);
  if (filters.department) params.set("department", filters.department);
  if (filters.field) params.set("field", filters.field);
  if (filters.discipline) params.set("discipline", filters.discipline);
  if (filters.major) params.set("major", filters.major);
  if (filters.country) params.set("country", filters.country);
  if (filters.verified === "1") params.set("verified", "1");
  if (filters.email === "1") params.set("email", "1");
  if (filters.papers === "1") params.set("papers", "1");
  if (filters.funding) params.set("funding", filters.funding);
  if (page > 1) params.set("page", String(page));
  const query = params.toString();
  return query ? `/professors?${query}` : "/professors";
}

function normalizeTerm(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export default async function ProfessorsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  await ensureSchema();
  const areaFilter = searchParams.area || searchParams.research;
  const q = searchParams.q?.trim();
  const universityId = searchParams.university;
  const verifiedOnly = searchParams.verified === "1";
  const emailOnly = searchParams.email === "1";
  const department = searchParams.department?.trim();
  const papersOnly = searchParams.papers === "1";
  const funding = searchParams.funding === "known" ? "known" : searchParams.funding === "unknown" ? "unknown" : "";
  const fieldId = searchParams.field || "";
  const disciplineId = searchParams.discipline || "";
  const majorId = searchParams.major || "";
  const country = searchParams.country?.trim() || "";
  const requested = Number(searchParams.page || "1");

  let aliasFieldIds: string[] = [];
  let aliasDisciplineIds: string[] = [];
  let aliasMajorIds: string[] = [];
  if (q && q.length >= 2) {
    try {
      const aliases = await prisma.academicAlias.findMany({
        where: { OR: [{ term: { contains: q } }, { normalizedTerm: { contains: normalizeTerm(q) } }] },
        select: { academicFieldId: true, disciplineId: true, majorId: true },
        take: 12,
      });
      aliasFieldIds = aliases.map((row) => row.academicFieldId).filter((id): id is string => Boolean(id));
      aliasDisciplineIds = aliases.map((row) => row.disciplineId).filter((id): id is string => Boolean(id));
      aliasMajorIds = aliases.map((row) => row.majorId).filter((id): id is string => Boolean(id));
    } catch {
      // aliases table may not exist yet
    }
  }

  const where = {
    AND: [
      areaFilter ? { researchAreas: { some: { researchArea: { name: areaFilter } } } } : {},
      universityId ? { universityId } : {},
      country ? { university: { country } } : {},
      department ? { department: { contains: department } } : {},
      fieldId ? { academicFields: { some: { academicFieldId: fieldId } } } : {},
      disciplineId ? { disciplines: { some: { disciplineId } } } : {},
      majorId ? { majors: { some: { majorId } } } : {},
      verifiedOnly ? { dataStatus: "verified" } : {},
      emailOnly ? { AND: [{ email: { not: null } }, { NOT: { email: "" } }] } : {},
      papersOnly
        ? {
            OR: [
              { AND: [{ publications: { not: null } }, { NOT: { publications: "" } }] },
              { dataStatus: "verified", publicationRows: { some: {} } },
            ],
          }
        : {},
      q
        ? {
            OR: [
              ...professorQueryWhere(q).OR,
              ...(aliasFieldIds.length ? [{ academicFields: { some: { academicFieldId: { in: aliasFieldIds } } } }] : []),
              ...(aliasDisciplineIds.length ? [{ disciplines: { some: { disciplineId: { in: aliasDisciplineIds } } } }] : []),
              ...(aliasMajorIds.length ? [{ majors: { some: { majorId: { in: aliasMajorIds } } } }] : []),
            ],
          }
        : {},
    ],
  };

  let total = 0;
  let professors: any[] = [];
  let areas: { name: string }[] = [];
  let universities: { id: string; name: string; country: string | null }[] = [];
  let fields: { id: string; name: string }[] = [];
  let disciplines: { id: string; name: string; academicFieldId: string }[] = [];
  let majors: { id: string; name: string; officialName: string | null; disciplineId: string }[] = [];

  try {
    total = await prisma.professor.count({ where });
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const page = Number.isFinite(requested) ? Math.min(Math.max(1, Math.floor(requested)), pageCount) : 1;

    let professorsRaw = await prisma.professor.findMany({
      where,
      include: {
        university: true,
        researchAreas: { include: { researchArea: true } },
      },
      orderBy: [{ name: "asc" }, { id: "asc" }],
      skip: (page - 1) * PAGE_SIZE,
      take: PAGE_SIZE,
    }).catch(async (err) => {
      console.error("Complex professors query failed, falling back:", err);
      return prisma.professor.findMany({
        where: {},
        include: { university: true },
        orderBy: { name: "asc" },
        skip: (page - 1) * PAGE_SIZE,
        take: PAGE_SIZE,
      });
    });

    const result = await Promise.all([
      Promise.resolve(professorsRaw),
      prisma.researchArea.findMany({ orderBy: { name: "asc" }, select: { name: true } }),
      prisma.university.findMany({ orderBy: { name: "asc" }, select: { id: true, name: true, country: true } }),
      prisma.academicField.findMany({ orderBy: { name: "asc" }, select: { id: true, name: true } }).catch(() => []),
      prisma.discipline.findMany({ orderBy: { name: "asc" }, select: { id: true, name: true, academicFieldId: true } }).catch(() => []),
      prisma.major.findMany({ orderBy: { name: "asc" }, select: { id: true, name: true, officialName: true, disciplineId: true } }).catch(() => []),
    ]);
    [professors, areas, universities, fields, disciplines, majors] = result as any;
  } catch (err) {
    console.error("Professors page query failed:", err);
    // fall back to empty so the page still renders
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Number.isFinite(requested) ? Math.min(Math.max(1, Math.floor(requested)), pageCount) : 1;

  const session = await getServerSession(authOptions);
  const student = session?.user?.email
    ? await prisma.student.findUnique({ where: { email: session.user.email } })
    : null;
  const filters = {
    q: q || "",
    area: areaFilter || "",
    university: universityId || "",
    department: department || "",
    verified: verifiedOnly ? "1" : "",
    email: emailOnly ? "1" : "",
    papers: papersOnly ? "1" : "",
    funding,
    field: fieldId,
    discipline: disciplineId,
    major: majorId,
    country,
  };
  const values: ProfessorFilterValues = {
    q: q || "",
    area: areaFilter || "",
    university: universityId || "",
    department: department || "",
    field: fieldId,
    discipline: disciplineId,
    major: majorId,
    country,
    verified: verifiedOnly,
    email: emailOnly,
    papers: papersOnly,
    funding,
  };
  const fieldName = fields.find((row) => row.id === fieldId)?.name;
  const disciplineName = disciplines.find((row) => row.id === disciplineId)?.name;
  const majorName = majors.find((row) => row.id === majorId);
  const universityName = universities.find((row) => row.id === universityId)?.name;
  const chips: { label: string; href: string }[] = [];
  const clearOne = (key: keyof typeof filters) => pageHref(1, { ...filters, [key]: "" });
  if (q) chips.push({ label: q, href: clearOne("q") });
  if (country) chips.push({ label: country, href: clearOne("country") });
  if (universityName) chips.push({ label: universityName, href: clearOne("university") });
  if (fieldName) chips.push({ label: fieldName, href: clearOne("field") });
  if (disciplineName) chips.push({ label: disciplineName, href: clearOne("discipline") });
  if (majorName) chips.push({ label: majorName.officialName || majorName.name, href: clearOne("major") });
  if (areaFilter) chips.push({ label: areaFilter, href: clearOne("area") });
  if (department) chips.push({ label: department, href: clearOne("department") });
  if (verifiedOnly) chips.push({ label: "Verified", href: clearOne("verified") });
  if (emailOnly) chips.push({ label: "Email available", href: clearOne("email") });
  if (papersOnly) chips.push({ label: "Publications", href: clearOne("papers") });
  const activeCount = chips.length;
  const start = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const end = total === 0 ? 0 : start + professors.length - 1;

  return (
    <div className="page-container py-8">
      <ProfessorFilters
        values={values}
        fields={fields}
        disciplines={disciplines}
        majors={majors.map((row) => ({ id: row.id, name: row.officialName || row.name, disciplineId: row.disciplineId }))}
        universities={universities.map((row) => ({ id: row.id, name: row.name, country: row.country || "" }))}
        areas={areas.map((row) => row.name)}
        countries={[...new Set(universities.map((row) => row.country).filter((item): item is string => Boolean(item)))].sort()}
        activeCount={activeCount}
      >
        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="section-title">Professors</h1>
            <p className="mt-1 text-sm text-[var(--gray-700)]">
              {total.toLocaleString()} professor{total === 1 ? "" : "s"} found
              {total > 0 ? ` · showing ${start}–${end}` : ""}
            </p>
          </div>
        </div>
        {chips.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2" aria-label="Active filters">
            {chips.map((chip) => (
              <Link key={chip.label + chip.href} href={chip.href} className="chip">
                {chip.label}
                <span aria-hidden="true">×</span>
                <span className="sr-only">Remove {chip.label}</span>
              </Link>
            ))}
            <Link href="/professors" className="text-sm font-medium text-[var(--teal-dark)] hover:underline">Clear all</Link>
          </div>
        )}
        <p className="mt-3 text-xs text-[var(--gray-500)]">Field, discipline, and major use stored links only. A missing combination is not guessed.</p>

        {professors.length === 0 ? (
          <div className="empty-state mt-6 rounded-lg border border-dashed border-[var(--gray-200)] bg-white px-6 py-12 text-center">
            <h2 className="text-lg font-semibold text-[var(--navy)]">No professors match these filters.</h2>
            <p className="mx-auto mt-2 max-w-md text-sm text-[var(--gray-700)]">Try a broader research area or remove one filter.</p>
            <Link href="/professors" className="btn-primary mt-5">Clear filters</Link>
          </div>
        ) : (
          <div className="reveal-grid mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {professors.map((p) => {
              const match = student?.researchInterests
                ? computeResearchMatch(
                    {
                      researchInterests: student.researchInterests,
                      major: student.major,
                      degree: student.degree,
                      skills: student.skills,
                      academicBackground: student.academicBackground,
                      projects: student.projects,
                      cvText: student.cvText,
                    },
                    {
                      researchInterests: p.researchInterests,
                      department: p.department,
                      publications: p.publications,
                      researchAreas: p.researchAreas.map((area: any) => ({
                        name: area.researchArea.name,
                        keywords: area.researchArea.keywords,
                      })),
                    }
                  )
                : null;
              const target = assessTarget({
                matchScore: match && !match.incomplete ? match.score : null,
                verified: p.dataStatus === "verified",
                hasEmail: Boolean(p.email?.trim()),
                newestYear: newestStoredYear(p.publications),
              });
              return (
                <ProfessorCard
                  key={p.id}
                  id={p.id}
                  name={p.name}
                  nameZh={p.nameZh}
                  position={p.position}
                  department={p.department}
                  universityName={p.university.name}
                  universityCity={p.university.city}
                  researchAreas={p.researchAreas.map((r: any) => r.researchArea.name)}
                  researchInterests={p.researchInterests}
                  verified={p.dataStatus === "verified"}
                  email={p.email}
                  recent={newestStoredYear(p.publications) ? `Newest stored year ${newestStoredYear(p.publications)}` : null}
                  matchScore={match && !match.incomplete ? match.score : null}
                  priority={target.level}
                />
              );
            })}
          </div>
        )}

        {total > 0 && (
          <nav className="mt-8 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between" aria-label="Professor pages">
            {page <= 1 ? (
              <span className="btn-secondary pointer-events-none opacity-40">Previous</span>
            ) : (
              <Link className="btn-secondary text-center" href={pageHref(page - 1, filters)}>Previous</Link>
            )}
            <p className="text-center text-sm text-[var(--gray-700)]">Page {page} of {pageCount}</p>
            {page >= pageCount ? (
              <span className="btn-secondary pointer-events-none opacity-40">Next</span>
            ) : (
              <Link className="btn-secondary text-center" href={pageHref(page + 1, filters)}>Next</Link>
            )}
          </nav>
        )}
      </ProfessorFilters>
    </div>
  );
}
