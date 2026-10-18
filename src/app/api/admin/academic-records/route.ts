import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { parseAdminPagination } from "@/lib/admin-pagination";

const ENTITIES = ["universities", "programs", "scholarships"] as const;
type Entity = (typeof ENTITIES)[number];

async function requireAdmin() {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email?.trim().toLowerCase();
  if (!email) return null;
  const student = await prisma.student.findUnique({
    where: { email },
    select: { id: true, email: true, role: true },
  });
  const configured = (process.env.ADMIN_EMAILS || "")
    .split(",").map((value) => value.trim().toLowerCase()).filter(Boolean).includes(email);
  return student && (student.role === "admin" || configured) ? student : null;
}

function validUrl(value: unknown): value is string | null {
  if (value === null || value === "") return true;
  if (typeof value !== "string" || value.length > 2000) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

export async function GET(req: NextRequest) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const params = req.nextUrl.searchParams;
  const entity = params.get("entity") as Entity | null;
  if (!entity || !ENTITIES.includes(entity)) return NextResponse.json({ error: "Choose universities, programs, or scholarships." }, { status: 400 });
  const { page, pageSize } = parseAdminPagination(params);
  const q = (params.get("q") || "").trim().slice(0, 120);
  const skip = (page - 1) * pageSize;

  if (entity === "universities") {
    const where = q ? { OR: [{ name: { contains: q } }, { city: { contains: q } }, { country: { contains: q } }, { province: { contains: q } }] } : undefined;
    const [totalCount, rows] = await Promise.all([
      prisma.university.count({ where }),
      prisma.university.findMany({ where, orderBy: { name: "asc" }, skip, take: pageSize, select: { id: true, name: true, city: true, country: true, officialUrl: true, sourceUrl: true, dataStatus: true, verificationStatus: true, updatedAt: true } }),
    ]);
    return NextResponse.json({ entity, rows, pagination: { page, pageSize, totalCount, totalPages: Math.max(1, Math.ceil(totalCount / pageSize)) } });
  }
  if (entity === "programs") {
    const where = q ? { OR: [{ major: { contains: q } }, { degree: { contains: q } }, { officialName: { contains: q } }, { university: { name: { contains: q } } }] } : undefined;
    const [totalCount, rows] = await Promise.all([
      prisma.program.count({ where }),
      prisma.program.findMany({ where, orderBy: [{ university: { name: "asc" } }, { major: "asc" }], skip, take: pageSize, select: { id: true, major: true, officialName: true, degree: true, sourceUrl: true, programUrl: true, verificationStatus: true, updatedAt: true, university: { select: { name: true } } } }),
    ]);
    return NextResponse.json({ entity, rows: rows.map((row) => ({ ...row, universityName: row.university.name })), pagination: { page, pageSize, totalCount, totalPages: Math.max(1, Math.ceil(totalCount / pageSize)) } });
  }
  const where = q ? { OR: [{ name: { contains: q } }, { type: { contains: q } }, { university: { name: { contains: q } } }] } : undefined;
  const [totalCount, rows] = await Promise.all([
    prisma.scholarship.count({ where }),
    prisma.scholarship.findMany({ where, orderBy: { name: "asc" }, skip, take: pageSize, select: { id: true, name: true, type: true, officialUrl: true, verificationStatus: true, dataStatus: true, deadline: true, updatedAt: true, university: { select: { name: true } } } }),
  ]);
  return NextResponse.json({ entity, rows: rows.map((row) => ({ ...row, universityName: row.university?.name || "Not linked" })), pagination: { page, pageSize, totalCount, totalPages: Math.max(1, Math.ceil(totalCount / pageSize)) } });
}

export async function PATCH(req: NextRequest) {
  const admin = await requireAdmin();
  if (!admin) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  const body = await req.json().catch(() => ({}));
  const entity = body.entity as Entity;
  const id = typeof body.id === "string" ? body.id.trim() : "";
  if (!ENTITIES.includes(entity) || !id) return NextResponse.json({ error: "Invalid record." }, { status: 400 });

  const allowedStatuses = ["UNVERIFIED", "PENDING_REVIEW", "VERIFIED", "NEEDS_REVIEW", "OUTDATED"];
  const verificationStatus = typeof body.verificationStatus === "string" ? body.verificationStatus : "";
  if (!allowedStatuses.includes(verificationStatus)) return NextResponse.json({ error: "Choose a valid verification status." }, { status: 400 });

  let before: Record<string, unknown> | null = null;
  let after: Record<string, unknown> | null = null;
  let entityType = "";
  if (entity === "universities") {
    if (!validUrl(body.officialUrl) || !validUrl(body.sourceUrl)) return NextResponse.json({ error: "URLs must be valid HTTP or HTTPS links." }, { status: 400 });
    const existing = await prisma.university.findUnique({ where: { id }, select: { id: true, verificationStatus: true, officialUrl: true, sourceUrl: true, dataStatus: true } });
    if (!existing) return NextResponse.json({ error: "University not found." }, { status: 404 });
    before = { verificationStatus: existing.verificationStatus, officialUrl: existing.officialUrl, sourceUrl: existing.sourceUrl, dataStatus: existing.dataStatus };
    const updated = await prisma.university.update({ where: { id }, data: { verificationStatus, officialUrl: body.officialUrl || null, sourceUrl: body.sourceUrl || null, verifiedAt: verificationStatus === "VERIFIED" ? new Date() : null } });
    after = { verificationStatus: updated.verificationStatus, officialUrl: updated.officialUrl, sourceUrl: updated.sourceUrl, dataStatus: updated.dataStatus };
    entityType = "University";
  } else if (entity === "programs") {
    if (!validUrl(body.sourceUrl) || !validUrl(body.programUrl)) return NextResponse.json({ error: "URLs must be valid HTTP or HTTPS links." }, { status: 400 });
    const existing = await prisma.program.findUnique({ where: { id }, select: { id: true, verificationStatus: true, sourceUrl: true, programUrl: true } });
    if (!existing) return NextResponse.json({ error: "Program not found." }, { status: 404 });
    before = { verificationStatus: existing.verificationStatus, sourceUrl: existing.sourceUrl, programUrl: existing.programUrl };
    const updated = await prisma.program.update({ where: { id }, data: { verificationStatus, sourceUrl: body.sourceUrl || null, programUrl: body.programUrl || null, lastVerifiedAt: verificationStatus === "VERIFIED" ? new Date() : null } });
    after = { verificationStatus: updated.verificationStatus, sourceUrl: updated.sourceUrl, programUrl: updated.programUrl };
    entityType = "Program";
  } else {
    if (!validUrl(body.officialUrl)) return NextResponse.json({ error: "URLs must be valid HTTP or HTTPS links." }, { status: 400 });
    const existing = await prisma.scholarship.findUnique({ where: { id }, select: { id: true, verificationStatus: true, officialUrl: true, dataStatus: true } });
    if (!existing) return NextResponse.json({ error: "Scholarship not found." }, { status: 404 });
    before = { verificationStatus: existing.verificationStatus, officialUrl: existing.officialUrl, dataStatus: existing.dataStatus };
    const updated = await prisma.scholarship.update({ where: { id }, data: { verificationStatus, officialUrl: body.officialUrl || null, lastVerifiedAt: verificationStatus === "VERIFIED" ? new Date() : null } });
    after = { verificationStatus: updated.verificationStatus, officialUrl: updated.officialUrl, dataStatus: updated.dataStatus };
    entityType = "Scholarship";
  }

  try {
    await prisma.changeLog.create({ data: {
      entityType, entityId: id, changeType: "ADMIN_ACADEMIC_RECORD_UPDATED",
      beforeJson: JSON.stringify(before), afterJson: JSON.stringify({ ...after, adminEmail: admin.email }),
      reviewedAt: new Date(), reviewedBy: admin.email,
    } });
  } catch (error) {
    console.error("[admin/academic-records] update saved but audit write failed", error);
  }
  return NextResponse.json({ id, entity, updated: true });
}
