import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

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

export async function GET() {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const requests = await prisma.applyRequest.findMany({
    orderBy: { updatedAt: "desc" },
    take: 100,
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
  });
  return NextResponse.json({ requests });
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
    select: { id: true, status: true, paymentStatus: true, studentNote: true },
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
    // Do not turn a successful request update into a failed response if the
    // optional audit table has not yet been deployed.
    console.error("[admin/apply-requests] status changed but audit write failed", error);
  }

  return NextResponse.json({ request: updated });
}
