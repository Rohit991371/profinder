import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { parseAdminPagination } from "@/lib/admin-pagination";

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
  const params = req.nextUrl.searchParams;
  const { page, pageSize } = parseAdminPagination(params);
  const query = (params.get("q") || "").trim().slice(0, 120);
  const where = query ? {
    OR: [
      { name: { contains: query } },
      { email: { contains: query } },
      { role: { contains: query } },
      { plan: { contains: query } },
    ],
  } : undefined;
  const [totalCount, users] = await Promise.all([
    prisma.student.count({ where }),
    prisma.student.findMany({
      where,
      orderBy: { createdAt: "desc" },
      skip: (page - 1) * pageSize,
      take: pageSize,
      select: { id: true, name: true, email: true, role: true, plan: true, createdAt: true, subscription: { select: { status: true } } },
    }),
  ]);
  return NextResponse.json({
    users,
    pagination: { page, pageSize, totalCount, totalPages: Math.max(1, Math.ceil(totalCount / pageSize)) },
  });
}

export async function PATCH(req: NextRequest) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const body = await req.json().catch(() => ({}));
  const id = typeof body.id === "string" ? body.id.trim() : "";
  const role = body.role;
  if (!id || (role !== "admin" && role !== "student")) {
    return NextResponse.json({ error: "Choose a valid user and role." }, { status: 400 });
  }
  const target = await prisma.student.findUnique({ where: { id }, select: { id: true, email: true, role: true } });
  if (!target) return NextResponse.json({ error: "User not found." }, { status: 404 });
  const configuredAdmins = (process.env.ADMIN_EMAILS || "").split(",").map((value) => value.trim().toLowerCase()).filter(Boolean);
  if (target.id === admin.id && role !== "admin") {
    return NextResponse.json({ error: "You cannot remove your own admin access." }, { status: 409 });
  }
  if (configuredAdmins.includes(target.email.toLowerCase()) && role !== "admin") {
    return NextResponse.json({ error: "This account is protected by ADMIN_EMAILS and cannot be demoted here." }, { status: 409 });
  }
  if (target.role === role) return NextResponse.json({ user: target, unchanged: true });
  try {
    const [, updated] = await prisma.$transaction([
      prisma.changeLog.create({
        data: {
          entityType: "Student",
          entityId: target.id,
          changeType: "ADMIN_USER_ROLE_UPDATED",
          beforeJson: JSON.stringify({ role: target.role }),
          afterJson: JSON.stringify({ role, adminEmail: admin.email, targetEmail: target.email }),
          reviewedAt: new Date(),
          reviewedBy: admin.email,
        },
      }),
      prisma.student.update({ where: { id }, data: { role }, select: { id: true, email: true, name: true, role: true } }),
    ]);
    return NextResponse.json({ user: updated });
  } catch (error) {
    console.error("[admin/users] role update failed", error);
    return NextResponse.json({ error: "Could not save this role change. Check that the audit log table exists, then try again." }, { status: 503 });
  }
}
