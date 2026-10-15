export type EmailInput = {
  studentName?: string | null;
  studentDegree?: string | null;
  studentMajor?: string | null;
  studentBackground?: string | null;
  studentInterests?: string | null;
  studentSkills?: string | null;
  studentProjects?: string | null;
  studentResearchExperience?: string | null;
  studentGpa?: string | null;
  studentTargetDegree?: string | null;
  studentIntake?: string | null;
  professorName: string;
  professorPosition?: string | null;
  professorDepartment?: string | null;
  professorInterests?: string | null;
  universityName: string;
  researchAreas?: string[];
  publications?: string | null;
  publicationTitles?: { title: string; year?: number | null }[];
  purpose?: string | null; // e.g. "supervision", "masters", "funded", "scholarship"
  tone?: string | null; // "formal", "concise", "enthusiastic"
};

export function profileReadyForEmail(input: EmailInput): string[] {
  const missing: string[] = [];
  if (!input.studentName?.trim()) missing.push("name");
  if (!input.studentDegree?.trim() && !input.studentTargetDegree?.trim()) missing.push("degree");
  if (!input.studentMajor?.trim()) missing.push("major");
  if (!input.studentInterests?.trim()) missing.push("research interests");
  return missing;
}

function lastName(name: string): string {
  return name.trim().split(/\s+/).slice(-1)[0] || name;
}

function findRelevantPublication(
  studentText: string,
  titles: { title: string; year?: number | null }[] | undefined
): { title: string; year?: number | null } | null {
  if (!titles?.length || !studentText.trim()) return null;
  const studentTokens = new Set(
    studentText.toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter((t) => t.length > 3)
  );
  let best: { title: string; year?: number | null; score: number } | null = null;
  for (const pub of titles) {
    const pubTokens = pub.title.toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter((t) => t.length > 3);
    let overlap = 0;
    for (const t of pubTokens) if (studentTokens.has(t)) overlap++;
    if (overlap > (best?.score || 0)) best = { ...pub, score: overlap };
  }
  return best && best.score >= 1 ? best : null;
}

export function generateOutreachEmail(input: EmailInput): {
  subject: string;
  body: string;
  source: "template" | "ai";
  missing: string[];
  relevantPublication?: string | null;
} {
  const missing = profileReadyForEmail(input);
  if (missing.length) {
    return { subject: "", body: "", source: "template", missing };
  }

  const last = lastName(input.professorName);
  const areas = (input.researchAreas ?? []).slice(0, 3).join(", ");
  const profFocus = input.professorInterests?.trim() || areas || "your research";
  const degree = input.studentTargetDegree?.trim() || input.studentDegree?.trim() || "graduate";
  const interests = input.studentInterests?.trim() || "";
  const projects = input.studentProjects?.trim();
  const skills = input.studentSkills?.trim();
  const experience = input.studentResearchExperience?.trim();
  const purpose = (input.purpose || "supervision").toLowerCase();

  const studentContext = [interests, projects, skills, experience, input.studentBackground].filter(Boolean).join(" ");
  const relevantPub = findRelevantPublication(studentContext, input.publicationTitles);

  let purposeLine = "I am writing to inquire about the possibility of research supervision.";
  if (purpose.includes("master") || purpose.includes("ms")) purposeLine = "I am applying for a master's program and would like to explore research opportunities in your group.";
  else if (purpose.includes("phd") || purpose.includes("doctor")) purposeLine = "I am seeking a PhD position and am interested in your research direction.";
  else if (purpose.includes("fund") || purpose.includes("scholarship")) purposeLine = "I am looking for a research opportunity that may include funding or scholarship support, if available.";

  const pubSentence = relevantPub
    ? ` Your work on “${relevantPub.title}”${relevantPub.year ? ` (${relevantPub.year})` : ""} appears closely related to my interests.`
    : input.publications?.trim()
    ? " I have reviewed the research summary on your profile and see alignment with my background."
    : "";

  const connection = projects
    ? ` My project on ${projects.split(/[.,;]/)[0].trim()} relates to this area.`
    : skills
    ? ` Skills I can contribute include ${skills}.`
    : experience
    ? ` Relevant experience: ${experience.split(/[.,;]/)[0].trim()}.`
    : "";

  const subject = `${degree} research inquiry – ${interests.split(",")[0]?.trim() || input.studentMajor}`;

  const body = `Dear Professor ${last},

My name is ${input.studentName}. I am a ${input.studentMajor} student preparing for a ${degree} and am interested in research opportunities at ${input.universityName}.

${purposeLine} The connection I see is between my interests in ${interests} and your work on ${profFocus}${areas ? ` (areas: ${areas})` : ""}.${pubSentence}${connection}

I would be grateful for any information about current openings, supervision availability, or next steps. I can provide a CV and a short research note upon request.

Thank you for your time.

Sincerely,
${input.studentName}`;

  return {
    subject,
    body,
    source: "template",
    missing: [],
    relevantPublication: relevantPub?.title || null,
  };
}

// Optional AI path – called only when key is present
export async function generateOutreachEmailAI(input: EmailInput): Promise<ReturnType<typeof generateOutreachEmail>> {
  const key = process.env.OPENAI_API_KEY;
  const base = process.env.OPENAI_BASE_URL || "https://api.openai.com/v1";
  const model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  if (!key) return generateOutreachEmail(input);

  const missing = profileReadyForEmail(input);
  if (missing.length) return { subject: "", body: "", source: "template", missing };

  const facts = {
    student: {
      name: input.studentName,
      degree: input.studentTargetDegree || input.studentDegree,
      major: input.studentMajor,
      interests: input.studentInterests,
      projects: input.studentProjects,
      skills: input.studentSkills,
      researchExperience: input.studentResearchExperience,
      gpa: input.studentGpa,
      background: input.studentBackground,
      intake: input.studentIntake,
    },
    professor: {
      name: input.professorName,
      position: input.professorPosition,
      department: input.professorDepartment,
      interests: input.professorInterests,
      areas: input.researchAreas,
      university: input.universityName,
      publications: input.publicationTitles?.slice(0, 3) || (input.publications ? [{ title: input.publications.slice(0, 120) }] : []),
    },
    purpose: input.purpose || "research supervision inquiry",
    tone: input.tone || "professional and concise",
  };

  const system = `You are an academic outreach assistant. Write a short, professional email from a student to a professor. Use ONLY the facts provided. Never invent publications, projects, achievements, funding details, or contact information. If a publication title is listed and relevant to the student's interests, you may reference it by title. Otherwise do not claim to have read any paper. Keep the email under 180 words. Output valid JSON with keys "subject" and "body".`;

  try {
    const res = await fetch(`${base}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${key}`,
      },
      body: JSON.stringify({
        model,
        temperature: 0.4,
        messages: [
          { role: "system", content: system },
          { role: "user", content: `Facts:\n${JSON.stringify(facts, null, 2)}\n\nWrite the email.` },
        ],
      }),
    });
    if (!res.ok) throw new Error("AI request failed");
    const data = await res.json();
    const text = data.choices?.[0]?.message?.content || "";
    const parsed = JSON.parse(text.replace(/```json|```/g, "").trim());
    if (parsed.subject && parsed.body) {
      return {
        subject: String(parsed.subject),
        body: String(parsed.body),
        source: "ai",
        missing: [],
        relevantPublication: input.publicationTitles?.[0]?.title || null,
      };
    }
  } catch {
    // fall through
  }
  return generateOutreachEmail(input);
}
