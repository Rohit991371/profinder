import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

async function requireAdmin() {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return null;
  const student = await prisma.student.findUnique({ where: { email: session.user.email } });
  const allow = (process.env.ADMIN_EMAILS || "").split(",").map((s) => s.trim().toLowerCase());
  if (!student || (student.role !== "admin" && !allow.includes(student.email.toLowerCase()))) return null;
  return student;
}

export async function GET() {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const professors = await prisma.professor.findMany({
    include: { university: true },
    orderBy: { name: "asc" },
    take: 200,
  });
  return NextResponse.json({
    professors: professors.map((p) => ({
      id: p.id,
      name: p.name,
      university: p.university.name,
      dataStatus: p.dataStatus,
      verifiedAt: p.verifiedAt,
      profileIsPersonal: p.profileIsPersonal,
    })),
  });
}

export async function PATCH(req: Request) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const body = await req.json().catch(() => ({}));
  const allowed = ["verified", "unverified", "needs_review", "outdated"];
  const id = typeof body.id === "string" ? body.id.trim() : "";
  const dataStatus = typeof body.dataStatus === "string" ? body.dataStatus : "";
  if (!id || !allowed.includes(dataStatus)) {
    return NextResponse.json({ error: "Invalid update" }, { status: 400 });
  }

  const existing = await prisma.professor.findUnique({
    where: { id },
    select: { id: true, dataStatus: true, verifiedAt: true },
  });
  if (!existing) return NextResponse.json({ error: "Professor not found." }, { status: 404 });
  if (existing.dataStatus === dataStatus) {
    return NextResponse.json({ id: existing.id, dataStatus: existing.dataStatus, unchanged: true });
  }

  const updated = await prisma.professor.update({
    where: { id },
    data: {
      dataStatus,
      verifiedAt: dataStatus === "verified" ? new Date() : null,
    },
  });

  try {
    await prisma.changeLog.create({
      data: {
        entityType: "Professor",
        entityId: id,
        changeType: "ADMIN_PROFESSOR_VERIFICATION_UPDATED",
        beforeJson: JSON.stringify({ dataStatus: existing.dataStatus, verifiedAt: existing.verifiedAt }),
        afterJson: JSON.stringify({ dataStatus: updated.dataStatus, verifiedAt: updated.verifiedAt, adminEmail: admin.email }),
        reviewedAt: new Date(),
        reviewedBy: admin.email,
      },
    });
  } catch (error) {
    console.error("[admin/professors] status changed but audit write failed", error);
  }

  return NextResponse.json({ id: updated.id, dataStatus: updated.dataStatus });
}
