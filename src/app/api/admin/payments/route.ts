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
  const tab = params.get("tab") || "requests";
  const { page, pageSize } = parseAdminPagination(params);
  const skip = (page - 1) * pageSize;
  const query = (params.get("q") || "").trim().slice(0, 120);
  const status = (params.get("status") || "all").trim();

  const [requestCount, paidRequestCount, paidRequestFees, subscriptionCount, activeSubscriptionCount, eventCount] = await Promise.all([
    prisma.applyRequest.count(),
    prisma.applyRequest.count({ where: { paymentStatus: "paid" } }),
    prisma.applyRequest.aggregate({ _sum: { feeCents: true }, where: { paymentStatus: "paid" } }),
    prisma.subscription.count(),
    prisma.subscription.count({ where: { status: { in: ["active", "trialing"] } } }),
    prisma.billingEvent.count(),
  ]);

  const summary = {
    applyRequestCount: requestCount,
    paidApplyRequestCount: paidRequestCount,
    recordedPaidApplyRequestFeesCents: paidRequestFees._sum.feeCents || 0,
    subscriptionCount,
    activeSubscriptionCount,
    billingEventCount: eventCount,
  };

  if (tab === "subscriptions") {
    const where = {
      ...(status !== "all" ? { status } : {}),
      ...(query ? { OR: [
        { id: { contains: query } },
        { plan: { contains: query } },
        { providerSubscriptionId: { contains: query } },
        { student: { name: { contains: query } } },
        { student: { email: { contains: query } } },
      ] } : {}),
    };
    const [total, rows] = await Promise.all([
      prisma.subscription.count({ where }),
      prisma.subscription.findMany({
        where, orderBy: { updatedAt: "desc" }, skip, take: pageSize,
        select: { id: true, studentId: true, plan: true, status: true, provider: true, providerSubscriptionId: true, priceId: true, currentPeriodStart: true, currentPeriodEnd: true, cancelAtPeriodEnd: true, createdAt: true, updatedAt: true, student: { select: { name: true, email: true } } },
      }),
    ]);
    return NextResponse.json({ tab, rows, total, page, pageSize, summary, limitation: "Subscription rows show current subscription state, not cash received. Invoice/charge amounts are not persisted in the current schema." });
  }

  if (tab === "events") {
    const where = {
      ...(status !== "all" ? { type: status } : {}),
      ...(query ? { OR: [{ id: { contains: query } }, { type: { contains: query } }] } : {}),
    };
    const [total, rows] = await Promise.all([
      prisma.billingEvent.count({ where }),
      prisma.billingEvent.findMany({ where, orderBy: { createdAt: "desc" }, skip, take: pageSize, select: { id: true, type: true, createdAt: true } }),
    ]);
    return NextResponse.json({ tab, rows, total, page, pageSize, summary, limitation: "Webhook event IDs and types are recorded for idempotency. Event payloads and invoice/charge amounts are not persisted, so this is not a complete financial ledger." });
  }

  const where = {
    ...(status !== "all" ? { paymentStatus: status } : {}),
    ...(query ? { OR: [
      { id: { contains: query } },
      { paymentRef: { contains: query } },
      { package: { contains: query } },
      { student: { name: { contains: query } } },
      { student: { email: { contains: query } } },
    ] } : {}),
  };
  const [total, rows] = await Promise.all([
    prisma.applyRequest.count({ where }),
    prisma.applyRequest.findMany({
      where, orderBy: { updatedAt: "desc" }, skip, take: pageSize,
      select: { id: true, studentId: true, feeCents: true, paymentStatus: true, paymentRef: true, package: true, status: true, createdAt: true, updatedAt: true, student: { select: { name: true, email: true } } },
    }),
  ]);
  return NextResponse.json({ tab: "requests", rows, total, page, pageSize, summary, limitation: "The amount shown is the recorded fee for requests marked paid. Subscription invoice/charge amounts are not persisted, so total Stripe cash revenue cannot be calculated from current records." });
}
