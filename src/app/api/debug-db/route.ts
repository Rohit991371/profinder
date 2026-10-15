import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

export async function GET() {
  const info: any = {
    vercel: !!process.env.VERCEL,
    vercelEnv: process.env.VERCEL_ENV,
    databaseUrlPrefix: (process.env.DATABASE_URL || "").slice(0, 40),
    cwd: process.cwd(),
  };

  const candidates = [
    path.join(process.cwd(), "prisma", "dev.db"),
    path.join(process.cwd(), ".next", "server", "prisma", "dev.db"),
    "/var/task/prisma/dev.db",
    "/tmp/profinder.db",
  ];

  info.files = candidates.map((p) => ({
    path: p,
    exists: fs.existsSync(p),
    size: fs.existsSync(p) ? fs.statSync(p).size : 0,
  }));

  try {
    const count = await prisma.university.count();
    info.universityCount = count;
  } catch (e: any) {
    info.queryError = e.message;
  }

  return NextResponse.json(info);
}
