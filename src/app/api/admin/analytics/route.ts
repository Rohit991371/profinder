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
  const range = req.nextUrl.searchParams.get("range") || "30d";
  const days = range === "7d" ? 7 : range === "all" ? null : 30;
  const start = days === null ? undefined : new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  const createdWhere = start ? { createdAt: { gte: start } } : undefined;
  const updatedWhere = start ? { updatedAt: { gte: start } } : undefined;
  const [totalUsers, newUsers, totalRequests, newRequests, paidRequests, unpaidRequests, revenue, totalSubscriptions, activeSubscriptions, universities, programs, professors, verifiedProfessors, scholarships] = await Promise.all([
    prisma.student.count(),
    prisma.student.count({ where: createdWhere }),
    prisma.applyRequest.count(),
    prisma.applyRequest.count({ where: createdWhere }),
    prisma.applyRequest.count({ where: { paymentStatus: "paid", ...(start ? { updatedAt: { gte: start } } : {}) } }),
    prisma.applyRequest.count({ where: { paymentStatus: { not: "paid" } } }),
    prisma.applyRequest.aggregate({ _sum: { feeCents: true }, where: { paymentStatus: "paid", ...(start ? { updatedAt: { gte: start } } : {}) } }),
    prisma.subscription.count(),
    prisma.subscription.count({ where: { status: { in: ["active", "trialing"] } } }),
    prisma.university.count(),
    prisma.program.count(),
    prisma.professor.count(),
    prisma.professor.count({ where: { dataStatus: "verified" } }),
    prisma.scholarship.count(),
  ]);
  return NextResponse.json({
    range,
    metrics: {
      totalUsers, newUsers, totalRequests, newRequests, paidRequests, unpaidRequests,
      confirmedApplyRevenueCents: revenue._sum.feeCents || 0,
      totalSubscriptions, activeSubscriptions, universities, programs, professors, verifiedProfessors, scholarships,
    },
    revenueNote: "Revenue includes only Apply for Me requests currently marked paid. Stripe subscription revenue is not calculated without a persisted invoice/charge ledger.",
  });
}
