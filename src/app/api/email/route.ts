import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { generateOutreachEmail, generateOutreachEmailAI } from "@/lib/email-generator";
import { consumeUsage, isPro, limitMessage } from "@/lib/billing/usage";
import { FEATURES } from "@/lib/billing/plans";

export async function POST(req: NextRequest) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Sign in to generate an email." }, { status: 401 });
  }

  const student = await prisma.student.findUnique({ where: { email: session.user.email } });
  if (!student) return NextResponse.json({ error: "Account not found" }, { status: 404 });

  const pro = await isPro(student);
  const usage = await consumeUsage(student.id, FEATURES.AI_OUTREACH, pro);
  if (!usage.ok) {
    return NextResponse.json({ error: limitMessage(FEATURES.AI_OUTREACH, usage.used, usage.limit || 0), code: "LIMIT" }, { status: 403 });
  }

  const body = await req.json().catch(() => ({}));
  const professorId = typeof body.professorId === "string" ? body.professorId : "";
  if (!professorId) return NextResponse.json({ error: "professorId required" }, { status: 400 });

  const professor = await prisma.professor.findUnique({
    where: { id: professorId },
    include: {
      university: true,
      researchAreas: { include: { researchArea: true } },
      publicationRows: { orderBy: { year: "desc" }, take: 5 },
    },
  });
  if (!professor) return NextResponse.json({ error: "Professor not found" }, { status: 404 });

  const input = {
    studentName: student.name,
    studentDegree: student.degree,
    studentMajor: student.major,
    studentBackground: student.academicBackground,
    studentInterests: student.researchInterests,
    studentSkills: student.skills,
    studentProjects: student.projects,
    studentResearchExperience: student.researchExperience,
    studentGpa: student.gpa,
    studentTargetDegree: student.targetDegreeLevel || student.degree,
    studentIntake: student.intake,
    professorName: professor.name,
    professorPosition: professor.position,
    professorDepartment: professor.department,
    professorInterests: professor.researchInterests,
    universityName: professor.university.name,
    researchAreas: professor.researchAreas.map((r) => r.researchArea.name),
    publications: professor.publications,
    publicationTitles: professor.publicationRows.map((p) => ({ title: p.title, year: p.year })),
    purpose: typeof body.purpose === "string" ? body.purpose : null,
    tone: typeof body.tone === "string" ? body.tone : null,
  };

  const email = process.env.OPENAI_API_KEY
    ? await generateOutreachEmailAI(input)
    : generateOutreachEmail(input);

  if (email.missing.length) {
    return NextResponse.json(
      {
        error: `Complete your profile first: ${email.missing.join(", ")}.`,
        missing: email.missing,
        code: "PROFILE_INCOMPLETE",
      },
      { status: 400 }
    );
  }

  return NextResponse.json({
    subject: email.subject,
    body: email.body,
    source: email.source,
    relevantPublication: email.relevantPublication || null,
    disclaimer: "Review and edit this draft before sending. PROFINDER does not send emails and never invents facts.",
    facts: [
      student.name ? `Your name: ${student.name}` : "Your name is missing",
      (student.targetDegreeLevel || student.degree) ? `Degree: ${student.targetDegreeLevel || student.degree}` : "Degree is missing",
      student.major ? `Major: ${student.major}` : "Major is missing",
      student.researchInterests ? `Your interests: ${student.researchInterests}` : "Research interests are missing",
      student.projects ? `Projects on file: ${student.projects.slice(0, 120)}…` : null,
      `Professor: ${professor.name}`,
      professor.researchInterests ? `Professor interests on file: ${professor.researchInterests}` : "No professor research summary is stored",
      professor.publicationRows.length ? `Stored publications: ${professor.publicationRows.length}` : "No individual publication titles stored",
      professor.email ? `Email on file: ${professor.email}` : "No email is stored for this professor",
    ].filter(Boolean),
  });
}
