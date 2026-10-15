import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import fs from "fs";
import path from "path";

// One-time recovery endpoint. Safe to call when University table is empty.
// After data is restored, this route can be deleted.
export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const secret = searchParams.get("secret");
  // Simple gate so random crawlers don't trigger it. Set IMPORT_SECRET in env or just use the hardcoded one-time value.
  if (secret !== "profinder-restore-2026") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const existing = await prisma.university.count();
    if (existing > 0) {
      return NextResponse.json({
        ok: true,
        message: "Database already has universities. Import skipped.",
        universities: existing,
        professors: await prisma.professor.count(),
      });
    }

    // --- Import universities + programs + scholarships (from prisma/import-unis.ts logic) ---
    const uniFile = path.join(process.cwd(), "data", "universities.json");
    if (!fs.existsSync(uniFile)) {
      return NextResponse.json({ error: "universities.json not found in deployment" }, { status: 500 });
    }
    const uniRows = JSON.parse(fs.readFileSync(uniFile, "utf8"));

    let uniAdded = 0;
    let progAdded = 0;
    let scholAdded = 0;

    for (const row of uniRows) {
      const found = await prisma.university.findFirst({ where: { name: row.name } });
      const data = {
        nameZh: row.nameZh ?? null,
        country: row.country || "China",
        province: row.province ?? null,
        city: row.city ?? null,
        officialUrl: row.officialUrl ?? null,
        description: row.description ?? null,
        agencyNumber: row.agencyNumber ?? null,
      };
      const uni = found
        ? await prisma.university.update({ where: { id: found.id }, data })
        : await prisma.university.create({ data: { name: row.name, ...data } });
      if (!found) uniAdded++;

      for (const p of row.programs || []) {
        const existing = await prisma.program.findFirst({
          where: { universityId: uni.id, degree: p.degree, major: p.major },
        });
        if (existing) {
          await prisma.program.update({ where: { id: existing.id }, data: p });
        } else {
          await prisma.program.create({ data: { ...p, universityId: uni.id } });
          progAdded++;
        }
      }

      for (const s of row.scholarships || []) {
        const existing = await prisma.scholarship.findFirst({
          where: { universityId: uni.id, name: s.name },
        });
        const payload = { ...s, dataStatus: s.dataStatus || "unverified" };
        if (existing) {
          await prisma.scholarship.update({ where: { id: existing.id }, data: payload });
        } else {
          await prisma.scholarship.create({ data: { ...payload, universityId: uni.id } });
          scholAdded++;
        }
      }
    }

    // --- Import professors (from prisma/import-professors.ts logic) ---
    const profFile = path.join(process.cwd(), "data", "professors.json");
    let profAdded = 0;
    let profSkipped = 0;
    if (fs.existsSync(profFile)) {
      const profRows = JSON.parse(fs.readFileSync(profFile, "utf8"));
      for (const row of profRows) {
        if (!row.university || !row.name) {
          profSkipped++;
          continue;
        }
        const uni = await prisma.university.findFirst({ where: { name: row.university } });
        if (!uni) {
          profSkipped++;
          continue;
        }
        const existing = await prisma.professor.findFirst({
          where: { universityId: uni.id, name: row.name },
        });
        const pdata = {
          nameZh: row.nameZh ?? null,
          position: row.position ?? null,
          school: row.school ?? null,
          department: row.department ?? null,
          email: row.email ?? null,
          profileUrl: row.profileUrl ?? null,
          researchInterests: row.researchInterests ?? null,
          publications: row.publications ?? null,
          dataStatus: row.dataStatus || "unverified",
          profileIsPersonal: false,
        };
        if (existing) {
          await prisma.professor.update({ where: { id: existing.id }, data: pdata });
        } else {
          await prisma.professor.create({ data: { ...pdata, name: row.name, universityId: uni.id } });
          profAdded++;
        }
      }
    }

    const finalUnis = await prisma.university.count();
    const finalProfs = await prisma.professor.count();

    return NextResponse.json({
      ok: true,
      message: "Import completed",
      universitiesAdded: uniAdded,
      programsAdded: progAdded,
      scholarshipsAdded: scholAdded,
      professorsAdded: profAdded,
      professorsSkipped: profSkipped,
      totals: { universities: finalUnis, professors: finalProfs },
    });
  } catch (err: any) {
    console.error("Import failed:", err);
    return NextResponse.json(
      { error: err?.message || String(err), stack: err?.stack },
      { status: 500 }
    );
  }
}
