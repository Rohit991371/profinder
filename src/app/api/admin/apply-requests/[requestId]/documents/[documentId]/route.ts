import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { readFile } from "fs/promises";
import path from "path";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

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

  const safeStoredName = path.basename(document.storedName);
  if (!safeStoredName || safeStoredName !== document.storedName) {
    return NextResponse.json({ error: "Invalid document path." }, { status: 400 });
  }
  try {
    const bytes = await readFile(path.join(process.cwd(), "data", "apply-uploads", safeStoredName));
    const safeDownloadName = document.name.replace(/[\r\n"]/g, "_").slice(0, 180) || "document";
    return new NextResponse(bytes, {
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": `attachment; filename="${safeDownloadName}"`,
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json(
      { error: "This file is not available in the current server storage. Its metadata is still preserved." },
      { status: 404 }
    );
  }
}
