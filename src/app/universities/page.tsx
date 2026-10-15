import type { Metadata } from "next";
import { ensureSchema, prisma } from "@/lib/prisma";
import Link from "next/link";
import { SITE } from "@/lib/seo";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { absolute: "Universities | ProFinder" },
  description: "Chinese universities with stored programs and professor records.",
  alternates: { canonical: `${SITE}/universities` },
};

export default async function UniversitiesPage() {
  await ensureSchema();

  let universities: { id: string; name: string; city: string | null; country: string | null }[] = [];
  try {
    universities = await prisma.university.findMany({
      orderBy: { name: "asc" },
      select: { id: true, name: true, city: true, country: true },
      take: 200,
    });
  } catch (err) {
    console.error("Universities query failed:", err);
  }

  return (
    <div className="page-container py-8">
      <h1 className="section-title">Universities</h1>
      <p className="mt-1 text-sm text-gray-600">{universities.length} stored</p>
      {universities.length === 0 ? (
        <p className="mt-8 text-sm text-gray-500">No universities loaded.</p>
      ) : (
        <ul className="mt-6 space-y-2">
          {universities.map((u) => (
            <li key={u.id}>
              <Link href={`/universities/${u.id}`} className="text-[var(--navy)] hover:underline">
                {u.name}
              </Link>
              <span className="text-sm text-gray-500"> · {[u.city, u.country].filter(Boolean).join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
