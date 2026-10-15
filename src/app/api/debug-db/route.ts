import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

export async function GET() {
  const info: any = {
    vercel: !!process.env.VERCEL,
    vercelEnv: process.env.VERCEL_ENV,
    databaseUrlPrefix: (process.env.DATABASE_URL || "").slice(0, 60),
    cwd: process.cwd(),
  };

  const candidates = [
    path.join(process.cwd(), "prisma", "dev.db"),
    "/var/task/prisma/dev.db",
    "/tmp/profinder.db",
  ];
  info.files = candidates.map((p) => ({
    path: p,
    exists: fs.existsSync(p),
    size: fs.existsSync(p) ? fs.statSync(p).size : 0,
  }));

  try { info.universityCount = await prisma.university.count(); } catch (e: any) { info.universityError = e.message; }
  try { info.professorCount = await prisma.professor.count(); } catch (e: any) { info.professorError = e.message; }
  try { info.programCount = await prisma.program.count(); } catch (e: any) { info.programError = e.message; }
  try { info.majorCount = await prisma.major.count(); } catch (e: any) { info.majorError = e.message; }
  try { info.scholarshipCount = await prisma.scholarship.count(); } catch (e: any) { info.scholarshipError = e.message; }

  try {
    const sample = await prisma.university.findMany({ take: 3, select: { id: true, name: true } });
    info.sampleUniversities = sample;
  } catch (e: any) {
    info.findManyError = e.message;
  }

  return NextResponse.json(info);
}
