import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { storeApplyFile } from "@/lib/apply-storage";

const KINDS = new Set(["passport", "transcript", "cv", "statement", "other"]);
const MAX_BYTES = 8 * 1024 * 1024;

export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  const student = await prisma.student.findUnique({ where: { email: session.user.email } });
  if (!student) return NextResponse.json({ error: "Account not found" }, { status: 404 });
  const request = await prisma.applyRequest.findFirst({ where: { id: params.id, studentId: student.id } });
  if (!request) return NextResponse.json({ error: "Request not found" }, { status: 404 });
  if (request.paymentStatus === "paid" && request.status !== "needs_action") {
    return NextResponse.json({ error: "Documents are locked after payment unless more are requested." }, { status: 409 });
  }

  const form = await req.formData();
  const kind = String(form.get("kind") || "other");
  const file = form.get("file");
  if (!KINDS.has(kind)) return NextResponse.json({ error: "Unknown document type." }, { status: 400 });
  if (!(file instanceof File) || file.size < 1) return NextResponse.json({ error: "Choose a file." }, { status: 400 });
  if (file.size > MAX_BYTES) return NextResponse.json({ error: "Each file must be 8 MB or smaller." }, { status: 400 });

  let stored: { storedName: string };
  try {
    stored = await storeApplyFile(request.id, file.name, Buffer.from(await file.arrayBuffer()));
  } catch (error) {
    const code = error instanceof Error ? error.message : "";
    if (code === "UNSUPPORTED_FILE") {
      return NextResponse.json({ error: "Upload a PDF, image, or Word file. HTML and SVG are not accepted." }, { status: 400 });
    }
    if (code === "PRIVATE_STORAGE_UNCONFIGURED") {
      return NextResponse.json({ error: "Private document storage is not configured." }, { status: 503 });
    }
    console.error("[apply/documents] upload failed", error);
    return NextResponse.json({ error: "Upload failed." }, { status: 500 });
  }

  try {
    const document = await prisma.applyDocument.create({
      data: { requestId: request.id, kind, name: file.name.slice(0, 180), storedName: stored.storedName, size: file.size },
    });
    if (request.status === "draft") {
      await prisma.applyRequest.update({ where: { id: request.id }, data: { status: "awaiting_payment" } });
    }
    return NextResponse.json({ document: { id: document.id, kind: document.kind, name: document.name, size: document.size } });
  } catch (error) {
    // Do not silently report success if metadata could not be saved.
    console.error("[apply/documents] metadata save failed after file upload", error);
    return NextResponse.json({ error: "The file was uploaded but its record could not be saved. Please contact support before retrying." }, { status: 500 });
  }
}
