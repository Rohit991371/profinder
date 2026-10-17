import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

async function requireAdmin() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email?.trim().toLowerCase();
  if (!email) return null;
  const student = await prisma.student.findUnique({ where: { email }, select: { id: true, email: true, role: true } });
  const configured = (process.env.ADMIN_EMAILS || "").split(",").map((value) => value.trim().toLowerCase()).filter(Boolean).includes(email);
  return student && (student.role === "admin" || configured) ? student : null;
}

export async function GET(req: NextRequest) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const query = (req.nextUrl.searchParams.get("q") || "").trim().slice(0, 100);
  try {
    const rows = await prisma.changeLog.findMany({
      where: query ? { OR: [{ entityType: { contains: query } }, { entityId: { contains: query } }, { changeType: { contains: query } }, { reviewedBy: { contains: query } }] } : undefined,
      orderBy: { detectedAt: "desc" },
      take: 200,
      select: { id: true, entityType: true, entityId: true, changeType: true, beforeJson: true, afterJson: true, sourceUrl: true, detectedAt: true, reviewedAt: true, reviewedBy: true },
    });
    return NextResponse.json({ entries: rows });
  } catch (error) {
    console.error("[admin/audit] could not load ChangeLog", error);
    return NextResponse.json({ error: "Audit log is unavailable. Verify the ChangeLog table exists in the connected database." }, { status: 503 });
  }
}
