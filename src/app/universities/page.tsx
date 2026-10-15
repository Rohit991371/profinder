import type { Metadata } from "next";
import { ensureSchema, prisma } from "@/lib/prisma";
import UniversityCard from "@/components/UniversityCard";
import PageHeader from "@/components/PageHeader";
import { SITE } from "@/lib/seo";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { absolute: "Universities | ProFinder" },
  description: "Chinese universities with stored programs and professor records. Open a university to see what is actually on file.",
  alternates: { canonical: `${SITE}/universities` },
};

export default async function UniversitiesPage() {
  await ensureSchema();

  let universities: any[] = [];
  let errorMsg = "";

  try {
    universities = await prisma.university.findMany({
      orderBy: { name: "asc" },
      select: {
        id: true,
        name: true,
        nameZh: true,
        city: true,
        province: true,
        description: true,
        agencyNumber: true,
        _count: { select: { professors: true, programs: true } },
      },
    });
  } catch (err: any) {
    console.error("Universities with counts failed, falling back:", err);
    errorMsg = err?.message || String(err);
    try {
      universities = await prisma.university.findMany({
        orderBy: { name: "asc" },
        select: {
          id: true,
          name: true,
          nameZh: true,
          city: true,
          province: true,
          description: true,
          agencyNumber: true,
        },
      });
      errorMsg = "";
    } catch (e2: any) {
      errorMsg = e2?.message || String(e2);
    }
  }

  return (
    <div className="page-container py-8">
      <PageHeader
        title="Universities"
        description="China · multidisciplinary graduate-study discovery"
      />
      {errorMsg && (
        <pre className="mt-4 overflow-auto rounded bg-red-50 p-3 text-xs text-red-800">{errorMsg}</pre>
      )}
      {universities.length === 0 ? (
        <div className="empty-state mt-8 rounded-lg border border-dashed border-[var(--gray-200)] bg-white px-6 py-12 text-center">
          <p className="text-sm text-[var(--gray-500)]">No universities yet.</p>
        </div>
      ) : (
        <div className="reveal-grid mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {universities.map((u) => (
            <UniversityCard
              key={u.id}
              id={u.id}
              name={u.name}
              nameZh={u.nameZh}
              city={u.city}
              province={u.province}
              description={u.description}
              agencyNumber={u.agencyNumber}
              professorCount={u._count?.professors ?? 0}
              programCount={u._count?.programs ?? 0}
            />
          ))}
        </div>
      )}
    </div>
  );
}
