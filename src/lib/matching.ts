/**
 * Transparent, deterministic research match.
 * Enhanced explanations that surface concrete connections from student projects,
 * skills, and experience against professor areas and stored publications.
 * Never invents facts.
 */

export type StudentProfile = {
  researchInterests?: string | null;
  major?: string | null;
  degree?: string | null;
  skills?: string | null;
  academicBackground?: string | null;
  projects?: string | null;
  cvText?: string | null;
  researchExperience?: string | null;
  tools?: string | null;
  programmingLanguages?: string | null;
  gpa?: string | null;
};

export type ProfessorProfile = {
  researchInterests?: string | null;
  department?: string | null;
  publications?: string | null;
  topics?: string[] | null;
  researchAreas?: { name: string; keywords?: string | null }[];
  position?: string | null;
};

export type MatchResult = {
  score: number;
  incomplete: boolean;
  missing: string[];
  breakdown: {
    interestOverlap: number;
    areaOverlap: number;
    topicOverlap: number;
    publicationOverlap: number;
    majorRelevance: number;
    degreeRelevance: number;
  };
  explanation: string;
  reasons: string[];
  strongAreas: string[];
  moderateAreas: string[];
  matchedKeywords: string[];
};

const STOP = new Set([
  "the", "and", "for", "with", "from", "that", "this", "your", "our", "are",
  "was", "were", "has", "have", "into", "onto", "about", "using", "based",
  "research", "studies", "study", "work", "works",
]);

function stem(token: string): string {
  if (token.endsWith("ies") && token.length > 4) return token.slice(0, -3) + "y";
  if (token.endsWith("es") && token.length > 4) return token.slice(0, -2);
  if (token.endsWith("s") && token.length > 4 && !token.endsWith("ss")) return token.slice(0, -1);
  return token;
}

export function tokenize(text: string | null | undefined): Set<string> {
  if (!text) return new Set();
  const out = new Set<string>();
  for (const raw of text.toLowerCase().replace(/[^a-z0-9+\s]/g, " ").split(/\s+/)) {
    if (raw.length < 3 || STOP.has(raw)) continue;
    out.add(stem(raw));
  }
  return out;
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0;
  let inter = 0;
  for (const t of a) if (b.has(t)) inter++;
  return inter / (a.size + b.size - inter);
}

function overlapTerms(a: Set<string>, b: Set<string>): string[] {
  const terms: string[] = [];
  for (const t of a) if (b.has(t)) terms.push(t);
  return terms.slice(0, 8);
}

export function profileGaps(student: StudentProfile): string[] {
  const missing: string[] = [];
  if (!student.researchInterests?.trim()) missing.push("research interests");
  if (!student.major?.trim()) missing.push("major");
  if (!student.degree?.trim()) missing.push("degree");
  return missing;
}

export function computeResearchMatch(
  student: StudentProfile,
  professor: ProfessorProfile
): MatchResult {
  const missing = profileGaps(student);
  const interestTokens = tokenize(student.researchInterests);
  const backgroundTokens = tokenize(
    [student.academicBackground, student.major, student.skills, student.projects, student.cvText, student.researchExperience, student.tools, student.programmingLanguages]
      .filter(Boolean)
      .join(" ")
  );
  const studentAll = new Set([...interestTokens, ...backgroundTokens]);

  const profInterest = tokenize(professor.researchInterests);
  const areaNames = (professor.researchAreas ?? []).map((a) => a.name);
  const areaTokens = tokenize(areaNames.join(" "));
  const keywordTokens = tokenize(
    (professor.researchAreas ?? []).map((a) => a.keywords ?? "").join(" ")
  );
  const storedTopics = tokenize((professor.topics ?? []).join(" "));
  const paperTokens = tokenize(professor.publications);
  const hasPapers = paperTokens.size > 0;
  const topicTokens = new Set([
    ...profInterest,
    ...keywordTokens,
    ...storedTopics,
    ...paperTokens,
  ]);

  const interestOverlap = jaccard(interestTokens, profInterest);
  const areaOverlap = jaccard(new Set([...interestTokens, ...backgroundTokens]), new Set([...areaTokens, ...keywordTokens, ...storedTopics]));
  const topicOverlap = jaccard(studentAll, topicTokens.size ? topicTokens : profInterest);
  const publicationOverlap = hasPapers ? jaccard(studentAll, paperTokens) : 0;

  const major = (student.major ?? "").toLowerCase();
  const dept = `${professor.department ?? ""} ${areaNames.join(" ")} ${professor.researchInterests ?? ""}`.toLowerCase();
  let majorRelevance = 0;
  if (major) {
    const majorTokens = tokenize(major);
    majorRelevance = jaccard(majorTokens, tokenize(dept));
    if (majorRelevance === 0 && dept.includes(major)) majorRelevance = 0.6;
  }

  const degree = (student.degree ?? "").toLowerCase();
  let degreeRelevance = 0;
  if (degree.includes("phd") || degree.includes("doctor")) degreeRelevance = 0.9;
  else if (degree.includes("master")) degreeRelevance = 0.8;
  else if (degree) degreeRelevance = 0.5;

  const scoreRaw = hasPapers
    ? interestOverlap * 0.4 + areaOverlap * 0.15 + topicOverlap * 0.2 + publicationOverlap * 0.15 + majorRelevance * 0.05 + degreeRelevance * 0.05
    : interestOverlap * 0.4 + areaOverlap * 0.2 + topicOverlap * 0.2 + majorRelevance * 0.1 + degreeRelevance * 0.1;

  const incomplete = missing.includes("research interests");
  const score = incomplete ? 0 : Math.round(Math.min(99, Math.max(0, scoreRaw * 100)));

  const strongAreas: string[] = [];
  const moderateAreas: string[] = [];
  for (const area of professor.researchAreas ?? []) {
    const sim = jaccard(studentAll, tokenize(`${area.name} ${area.keywords ?? ""}`));
    if (sim >= 0.2 || interestTokens.has(stem(area.name.toLowerCase()))) strongAreas.push(area.name);
    else if (sim >= 0.08) moderateAreas.push(area.name);
  }

  const matchedKeywords = overlapTerms(studentAll, new Set([...profInterest, ...keywordTokens, ...areaTokens]));
  const reasons: string[] = [];
  if (strongAreas.length) reasons.push(`Research area overlap: ${strongAreas.join(", ")}`);
  if (matchedKeywords.length) reasons.push(`Shared terms: ${matchedKeywords.join(", ")}`);
  if (major && majorRelevance >= 0.2) reasons.push(`Your ${student.major} background is relevant to this department/field`);

  // Concrete connection from student projects/experience
  if (student.projects?.trim() && matchedKeywords.length) {
    const projSnippet = student.projects.trim().split(/[.,;]/)[0].slice(0, 80);
    reasons.push(`Your project (“${projSnippet}…”) shares terms with this professor’s work`);
  } else if (student.researchExperience?.trim() && matchedKeywords.length) {
    reasons.push("Stored research experience overlaps with the professor’s areas");
  }

  if (professor.publications?.trim()) {
    reasons.push("Stored publication summary was included in topic similarity. This does not mean a specific paper was read.");
  } else {
    reasons.push("Topic score uses stored research interests and area keywords. Individual publications are not on file.");
  }

  const explanation = incomplete
    ? "Add research interests on your profile before a personalized match can be calculated."
    : reasons.slice(0, 4).join(". ") + ".";

  return {
    score,
    incomplete,
    missing,
    breakdown: {
      interestOverlap: Math.round(interestOverlap * 100),
      areaOverlap: Math.round(areaOverlap * 100),
      topicOverlap: Math.round(topicOverlap * 100),
      publicationOverlap: Math.round(publicationOverlap * 100),
      majorRelevance: Math.round(majorRelevance * 100),
      degreeRelevance: Math.round(degreeRelevance * 100),
    },
    explanation,
    reasons,
    strongAreas,
    moderateAreas,
    matchedKeywords,
  };
}
