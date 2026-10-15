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

  let universities: { id: string; name: string }[] = [];
  let errorMsg = "";
  try {
    universities = await prisma.university.findMany({
      orderBy: { name: "asc" },
      select: { id: true, name: true },
      take: 50,
    });
  } catch (err: any) {
    errorMsg = err?.message || String(err);
    console.error("Universities query failed:", err);
  }

  return (
    <div className="page-container py-8">
      <h1 className="section-title">Universities</h1>
      <p className="mt-1 text-sm text-gray-600">{universities.length} stored</p>
      {errorMsg && (
        <pre className="mt-4 overflow-auto rounded bg-red-50 p-3 text-xs text-red-800">{errorMsg}</pre>
      )}
      {universities.length === 0 && !errorMsg ? (
        <p className="mt-8 text-sm text-gray-500">No universities loaded (query returned empty).</p>
      ) : (
        <ul className="mt-6 space-y-2">
          {universities.map((u) => (
            <li key={u.id}>
              <Link href={`/universities/${u.id}`} className="text-[var(--navy)] hover:underline">
                {u.name}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
