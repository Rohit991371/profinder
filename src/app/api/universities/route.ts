import { NextRequest, NextResponse } from "next/server";
import { ensureSchema, prisma } from "@/lib/prisma";

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get("q")?.trim();

  try {
    await ensureSchema();

    // Prefer a full query; fall back to a minimal one if columns or relations are missing in production.
    let universities;
    try {
      universities = await prisma.university.findMany({
        where: q
          ? {
              OR: [
                { name: { contains: q, mode: "insensitive" } },
                { nameZh: { contains: q, mode: "insensitive" } },
                { city: { contains: q, mode: "insensitive" } },
                { province: { contains: q, mode: "insensitive" } },
                { country: { contains: q, mode: "insensitive" } },
                { description: { contains: q, mode: "insensitive" } },
              ],
            }
          : undefined,
        orderBy: { name: "asc" },
        take: q ? 40 : 120,
        select: {
          id: true,
          name: true,
          nameZh: true,
          city: true,
          province: true,
          country: true,
          description: true,
          agencyNumber: true,
          _count: { select: { professors: true, programs: true } },
        },
      });
    } catch (inner) {
      console.error("Full university query failed, falling back:", inner);
      universities = await prisma.university.findMany({
        where: q
          ? {
              OR: [
                { name: { contains: q, mode: "insensitive" } },
                { city: { contains: q, mode: "insensitive" } },
                { country: { contains: q, mode: "insensitive" } },
              ],
            }
          : undefined,
        orderBy: { name: "asc" },
        take: q ? 40 : 120,
        select: {
          id: true,
          name: true,
          city: true,
          country: true,
        },
      });
    }

    return NextResponse.json({ universities, capped: true });
  } catch (err) {
    console.error(err);
    return NextResponse.json({ error: "Failed to fetch universities" }, { status: 500 });
  }
}
