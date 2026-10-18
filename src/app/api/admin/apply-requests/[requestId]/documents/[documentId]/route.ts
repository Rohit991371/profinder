import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { readApplyFile } from "@/lib/apply-storage";

export async function GET(
  _req: NextRequest,
  { params }: { params: { requestId: string; documentId: string } }
) {
  const session = await getServerSession(authOptions);
  const email = session?.user?.email?.trim().toLowerCase();
  if (!email) return NextResponse.json({ error: "Sign in first." }, { status: 401 });

  const student = await prisma.student.findUnique({
    where: { email },
    select: { email: true, role: true },
  });
  const configuredAdmin = (process.env.ADMIN_EMAILS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean)
    .includes(email);
  if (!student || (student.role !== "admin" && !configuredAdmin)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const document = await prisma.applyDocument.findFirst({
    where: { id: params.documentId, requestId: params.requestId },
    select: { name: true, storedName: true, kind: true },
  });
  if (!document) return NextResponse.json({ error: "Document not found." }, { status: 404 });

  const file = await readApplyFile(params.requestId, document.storedName);
  if (!file) {
    return NextResponse.json(
      { error: "This file is not available in persistent storage. Its document record has been preserved." },
      { status: 404 }
    );
  }

  const safeDownloadName = document.name.replace(/[\r\n"]/g, "_").slice(0, 180) || "document";
  return new NextResponse(file.stream, {
    headers: {
      "Content-Type": file.contentType,
      "Content-Disposition": `attachment; filename="${safeDownloadName}"`,
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
