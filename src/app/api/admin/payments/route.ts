import { NextResponse } from "next/server";
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

export async function GET() {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const [requests, subscriptions] = await Promise.all([
    prisma.applyRequest.findMany({
      orderBy: { updatedAt: "desc" },
      take: 200,
      select: { id: true, studentId: true, feeCents: true, paymentStatus: true, paymentRef: true, package: true, status: true, createdAt: true, updatedAt: true, student: { select: { name: true, email: true } } },
    }),
    prisma.subscription.findMany({
      orderBy: { updatedAt: "desc" },
      take: 200,
      select: { id: true, studentId: true, plan: true, status: true, provider: true, providerSubscriptionId: true, priceId: true, currentPeriodStart: true, currentPeriodEnd: true, cancelAtPeriodEnd: true, createdAt: true, updatedAt: true, student: { select: { name: true, email: true } } },
    }),
  ]);
  return NextResponse.json({
    requests: requests.map((row) => ({ ...row, recordType: "apply_request" })),
    subscriptions: subscriptions.map((row) => ({ ...row, recordType: "subscription" })),
    limitation: "The current schema stores Apply for Me payment status and subscription state, but not a complete Stripe charge/invoice ledger. Subscription rows are not represented as cash revenue.",
  });
}
