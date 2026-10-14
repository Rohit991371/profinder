import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { discoveryTokens, professorQueryWhere } from "@/lib/discovery";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function normalize(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

type Hit = { type: string; label: string; subtitle: string; href: string };

export async function GET(request: NextRequest) {
  const raw = request.nextUrl.searchParams.get("q")?.trim() || "";
  if (raw.length < 2 || raw.length > 120) return NextResponse.json({ results: [] });
  const q = normalize(raw);

  const tokens = discoveryTokens(raw);
  const programWhere = tokens.length >= 2
    ? { AND: tokens.map((token) => ({ OR: [{ major: { contains: token } }, { degree: { contains: token } }, { officialName: { contains: token } }, { university: { name: { contains: token } } }] })) }
    : { OR: [{ major: { contains: raw } }, { degree: { contains: raw } }, { officialName: { contains: raw } }, { university: { name: { contains: raw } } }] };

  const [majors, disciplines, fields, programs, professors, universities, areas, topics, aliases] = await Promise.all([
    prisma.major.findMany({
      where: { OR: [{ name: { contains: raw } }, { officialName: { contains: raw } }] },
      select: { id: true, name: true, officialName: true, discipline: { select: { name: true, academicField: { select: { name: true } } } } },
      take: 4,
      orderBy: { name: "asc" },
    }),
    prisma.discipline.findMany({
      where: { name: { contains: raw } },
      select: { id: true, name: true, academicField: { select: { name: true } } },
      take: 4,
    }),
    prisma.academicField.findMany({
      where: { name: { contains: raw } },
      select: { id: true, name: true },
      take: 3,
    }),
    prisma.program.findMany({
      where: programWhere,
      select: { id: true, major: true, degree: true, university: { select: { name: true, id: true, country: true } } },
      take: 5,
    }),
    prisma.professor.findMany({
      where: professorQueryWhere(raw),
      select: { id: true, name: true, department: true, university: { select: { name: true } } },
      take: 5,
    }),
    prisma.university.findMany({
      where: { OR: [{ name: { contains: raw } }, { nameZh: { contains: raw } }, { city: { contains: raw } }] },
      select: { id: true, name: true, city: true, country: true },
      take: 4,
    }),
    prisma.researchArea.findMany({
      where: tokens.length >= 2
        ? { OR: tokens.map((token) => ({ OR: [{ name: { contains: token } }, { keywords: { contains: token } }] })) }
        : { OR: [{ name: { contains: raw } }, { keywords: { contains: raw } }] },
      select: { name: true },
      take: 4,
    }),
    prisma.topic.findMany({
      where: tokens.length >= 2
        ? { AND: tokens.map((token) => ({ name: { contains: token } })) }
        : { name: { contains: raw } },
      select: { name: true },
      take: 3,
    }),
    prisma.academicAlias.findMany({
      where: { OR: [{ term: { contains: raw } }, { normalizedTerm: { contains: q } }] },
      select: { term: true, kind: true, academicFieldId: true, disciplineId: true, majorId: true, academicField: { select: { name: true } }, discipline: { select: { name: true } }, major: { select: { name: true } } },
      take: 4,
    }),
  ]);

  const results: Hit[] = [
    ...universities.map((x) => ({
      type: "university",
      label: x.name,
      subtitle: `University · ${[x.city, x.country].filter(Boolean).join(" · ") || "Location not stored"}`,
      href: "/universities/" + x.id,
    })),
    ...professors.map((x) => ({
      type: "professor",
      label: x.name,
      subtitle: `Professor · ${x.university.name}${x.department ? ` · ${x.department}` : ""}`,
      href: "/professors/" + x.id,
    })),
    ...programs.map((x) => ({
      type: "program",
      label: `${x.degree} · ${x.major}`,
      subtitle: `Program · ${x.university.name}${x.university.country ? ` · ${x.university.country}` : ""}`,
      href: "/universities/" + x.university.id + "#programs",
    })),
    ...areas.map((x) => ({
      type: "research",
      label: x.name,
      subtitle: "Research area",
      href: "/professors?area=" + encodeURIComponent(x.name),
    })),
    ...topics.map((x) => ({
      type: "research",
      label: x.name,
      subtitle: "Stored topic",
      href: "/professors?q=" + encodeURIComponent(x.name),
    })),
    ...fields.map((x) => ({
      type: "field",
      label: x.name,
      subtitle: "Academic field",
      href: "/professors?field=" + encodeURIComponent(x.id),
    })),
    ...disciplines.map((x) => ({
      type: "discipline",
      label: x.name,
      subtitle: `Discipline · ${x.academicField.name}`,
      href: "/professors?discipline=" + encodeURIComponent(x.id),
    })),
    ...majors.map((x) => ({
      type: "major",
      label: x.officialName || x.name,
      subtitle: `Major · ${x.discipline.academicField.name} · ${x.discipline.name}`,
      href: "/professors?major=" + encodeURIComponent(x.id),
    })),
    ...aliases.flatMap((x) => {
      const target = x.major?.name || x.discipline?.name || x.academicField?.name || x.term;
      if (x.majorId) return [{ type: "alias", label: x.term, subtitle: `Stored alias · major · ${target}`, href: "/professors?major=" + encodeURIComponent(x.majorId) }];
      if (x.disciplineId) return [{ type: "alias", label: x.term, subtitle: `Stored alias · discipline · ${target}`, href: "/professors?discipline=" + encodeURIComponent(x.disciplineId) }];
      if (x.academicFieldId) return [{ type: "alias", label: x.term, subtitle: `Stored alias · field · ${target}`, href: "/professors?field=" + encodeURIComponent(x.academicFieldId) }];
      return [];
    }),
  ];

  const seen = new Set<string>();
  const unique = results.filter((row) => {
    const key = row.type + row.href + row.label;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const weight: Record<string, number> = { university: 4, professor: 5, program: 4, research: 3, field: 2, discipline: 2, major: 2, alias: 1 };
  unique.sort((a, b) => {
    const aq = a.label.toLowerCase().includes(raw.toLowerCase()) ? 2 : 0;
    const bq = b.label.toLowerCase().includes(raw.toLowerCase()) ? 2 : 0;
    return bq + (weight[b.type] || 0) - (aq + (weight[a.type] || 0));
  });

  return NextResponse.json({ results: unique.slice(0, 12), query: q });
}
