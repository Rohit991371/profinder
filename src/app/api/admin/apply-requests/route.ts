import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { parseAdminPagination } from "@/lib/admin-pagination";

const REQUEST_STATUSES = [
  "draft",
  "awaiting_payment",
  "preparing",
  "needs_action",
  "ready_for_review",
  "submit_requested",
  "submitted",
  "completed",
  "cancelled",
] as const;

async function requireAdmin() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email?.trim().toLowerCase();
  if (!email) return null;
  const student = await prisma.student.findUnique({
    where: { email },
    select: { id: true, email: true, role: true },
  });
  const configured = (process.env.ADMIN_EMAILS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean)
    .includes(email);
  return student && (student.role === "admin" || configured) ? student : null;
}

export async function GET(req: NextRequest) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const params = req.nextUrl.searchParams;
  const { page, pageSize } = parseAdminPagination(params);
  const statusParam = (params.get("status") || "").trim();
  const query = (params.get("q") || "").trim().slice(0, 120);

  if (statusParam && statusParam !== "all" && !REQUEST_STATUSES.includes(statusParam as (typeof REQUEST_STATUSES)[number])) {
    return NextResponse.json({ error: "Choose a valid request status." }, { status: 400 });
  }

  const where = {
    ...(statusParam && statusParam !== "all" ? { status: statusParam } : {}),
    ...(query ? {
      OR: [
        { id: { contains: query } },
        { student: { is: { name: { contains: query } } } },
        { student: { is: { email: { contains: query } } } },
        { items: { some: { university: { is: { name: { contains: query } } } } } },
      ],
    } : {}),
  };

  const [totalCount, statusCounts, requests] = await Promise.all([
    prisma.applyRequest.count({ where }),
    Promise.all(REQUEST_STATUSES.map(async (status) => [
      status,
      await prisma.applyRequest.count({ where: { status } }),
    ] as const)),
    prisma.applyRequest.findMany({
      where,
      orderBy: { updatedAt: "desc" },
      skip: (page - 1) * pageSize,
      take: pageSize,
      include: {
        student: { select: { id: true, name: true, email: true } },
        items: {
          orderBy: { createdAt: "asc" },
          include: { university: { select: { id: true, name: true, country: true, city: true } } },
        },
        documents: {
          orderBy: { createdAt: "desc" },
          select: { id: true, kind: true, name: true, size: true, createdAt: true },
        },
      },
    }),
  ]);

  const requestIds = requests.map((request) => request.id);
  const latestNotes: Record<string, string> = {};
  if (requestIds.length) {
    try {
      const changes = await prisma.changeLog.findMany({
        where: { entityType: "ApplyRequest", entityId: { in: requestIds }, changeType: "ADMIN_STATUS_UPDATED" },
        orderBy: { detectedAt: "desc" },
        select: { entityId: true, afterJson: true },
      });
      for (const change of changes) {
        if (latestNotes[change.entityId]) continue;
        try {
          const parsed = JSON.parse(change.afterJson || "{}") as { note?: unknown };
          if (typeof parsed.note === "string") latestNotes[change.entityId] = parsed.note;
        } catch { /* Ignore malformed historical audit entries. */ }
      }
    } catch (error) {
      console.error("[admin/apply-requests] could not load internal notes", error);
    }
  }

  return NextResponse.json({
    requests: requests.map((request) => ({ ...request, adminNote: latestNotes[request.id] || "" })),
    pagination: {
      page,
      pageSize,
      totalCount,
      totalPages: Math.max(1, Math.ceil(totalCount / pageSize)),
    },
    statusCounts: Object.fromEntries(statusCounts),
    filters: { status: statusParam || "all", q: query },
  });
}

export async function PATCH(req: NextRequest) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const body = await req.json().catch(() => ({}));
  const id = typeof body.id === "string" ? body.id.trim() : "";
  const status = typeof body.status === "string" ? body.status : "";
  const note = typeof body.note === "string" ? body.note.trim().slice(0, 2000) : "";
  if (!id || !REQUEST_STATUSES.includes(status as (typeof REQUEST_STATUSES)[number])) {
    return NextResponse.json({ error: "Choose a valid request and status." }, { status: 400 });
  }

  const existing = await prisma.applyRequest.findUnique({
    where: { id },
    select: { id: true, status: true, paymentStatus: true },
  });
  if (!existing) return NextResponse.json({ error: "Apply for Me request not found." }, { status: 404 });
  if (status === "completed" && existing.paymentStatus !== "paid") {
    return NextResponse.json({ error: "An unpaid request cannot be marked completed." }, { status: 409 });
  }

  const updated = await prisma.applyRequest.update({
    where: { id },
    data: { status },
    select: { id: true, status: true, updatedAt: true },
  });

  try {
    await prisma.changeLog.create({
      data: {
        entityType: "ApplyRequest",
        entityId: id,
        changeType: "ADMIN_STATUS_UPDATED",
        beforeJson: JSON.stringify({ status: existing.status }),
        afterJson: JSON.stringify({ status, note, adminEmail: admin.email }),
        reviewedAt: new Date(),
        reviewedBy: admin.email,
      },
    });
  } catch (error) {
    console.error("[admin/apply-requests] status changed but audit write failed", error);
  }

  return NextResponse.json({ request: updated });
}
