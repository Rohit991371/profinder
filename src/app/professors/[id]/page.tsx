function sourceDisplay(profileUrl: string | null, dataStatus: string) {
  if (!profileUrl) {
    return {
      source: "Not recorded",
      verification: "Verification source unavailable",
      href: null as string | null,
      tone: "text-gray-600",
    };
  }
  if (profileUrl.includes("openalex.org")) {
    return {
      source: "OpenAlex",
      verification: "Academic database source — not independently verified",
      href: profileUrl,
      tone: "text-amber-800",
    };
  }
  if (dataStatus === "verified") {
    return {
      source: "Official university faculty page",
      verification: "Verified from official university source",
      href: profileUrl,
      tone: "text-emerald-800",
    };
  }
  return {
    source: "Recorded profile URL",
    verification: "Not independently verified",
    href: profileUrl,
    tone: "text-gray-700",
  };
}

import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getServerSession } from "next-auth";
import { prisma } from "@/lib/prisma";
import { authOptions } from "@/lib/auth";
import { computeResearchMatch, profileGaps } from "@/lib/matching";
import { assessTarget, fundingStatement, newestStoredYear, timelineGroups } from "@/lib/professor-assessment";
import { isPro } from "@/lib/billing/usage";
import { SITE, clip } from "@/lib/seo";
import MatchScore from "@/components/MatchScore";
import SaveToTrackerButton from "@/components/SaveToTrackerButton";
import EmailGeneratorButton from "@/components/EmailGeneratorButton";
import ProposalButton from "@/components/ProposalButton";

export const dynamic = "force-dynamic";

type Props = { params: { id: string } };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const prof = await prisma.professor.findUnique({
    where: { id: params.id },
    include: { university: true, researchAreas: { include: { researchArea: true } } },
  });
  if (!prof) return { title: "Professor", robots: { index: false, follow: false } };
  const area = prof.researchAreas[0]?.researchArea.name;
  const title = `Professor ${prof.name}${area ? ` — ${area}` : ""} | ${prof.university.name} | ProFinder`;
  const description = clip(
    `${prof.name} is listed at ${prof.university.name}${prof.department ? `, ${prof.department}` : ""}. ${prof.researchInterests || "Research interests are not stored."} Status: ${prof.dataStatus}.`
  );
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: `${SITE}/professors/${prof.id}` },
    robots: { index: true, follow: true },
  };
}

function publicationLines(value: string | null) {
  if (!value) return [];
  return value
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(/^\d+\.\s*/, ""));
}

export default async function ProfessorDetailPage({ params }: Props) {
  const prof = await prisma.professor.findUnique({
    where: { id: params.id },
    include: {
      university: true,
      researchAreas: { include: { researchArea: true } },
    },
  });
  if (!prof) notFound();

  const publicationRecords = prof.dataStatus === "verified"
    ? await prisma.publication.findMany({
        where: { professorId: prof.id },
        orderBy: [{ year: "desc" }, { title: "asc" }],
        take: 8,
      })
    : [];

  const session = await getServerSession(authOptions);
  const student = session?.user?.email
    ? await prisma.student.findUnique({ where: { email: session.user.email } })
    : null;

  const pro = student ? await isPro(student) : false;
  const gaps = student
    ? profileGaps({
        researchInterests: student.researchInterests,
        major: student.major,
        degree: student.degree,
      })
    : [];

  const match = student
    ? computeResearchMatch(
        {
          researchInterests: student.researchInterests,
          major: student.major,
          degree: student.degree,
          skills: student.skills,
          academicBackground: student.academicBackground,
          projects: student.projects,
          cvText: student.cvText,
          researchExperience: student.researchExperience,
          tools: student.tools,
          programmingLanguages: student.programmingLanguages,
          gpa: student.gpa,
        },
        {
          researchInterests: prof.researchInterests,
          department: prof.department,
          position: prof.position,
          publications: prof.publications,
          researchAreas: prof.researchAreas.map((r) => ({
            name: r.researchArea.name,
            keywords: r.researchArea.keywords,
          })),
        }
      )
    : null;

  const papers = publicationLines(prof.publications);
  const newestYear = newestStoredYear(prof.publications, publicationRecords.map((paper) => paper.year));
  const target = assessTarget({
    matchScore: match && !match.incomplete ? match.score : null,
    verified: prof.dataStatus === "verified",
    hasEmail: Boolean(prof.email?.trim()),
    newestYear,
  });
  const funding = fundingStatement();
  const timeline = timelineGroups([
    ...publicationRecords.map((paper) => ({ title: paper.title, year: paper.year })),
    ...papers.map((paper) => ({ title: paper, year: newestStoredYear(paper) })),
  ]);

  const personalProfile = Boolean(prof.profileUrl && prof.profileIsPersonal);
  const canonical = `${SITE}/professors/${prof.id}`;
  const personLd = {
    "@context": "https://schema.org",
    "@type": "Person",
    name: prof.name,
    url: canonical,
    ...(prof.position ? { jobTitle: prof.position } : {}),
    ...(prof.email ? { email: prof.email } : {}),
    ...(prof.orcid ? { sameAs: [prof.orcid.startsWith("http") ? prof.orcid : `https://orcid.org/${prof.orcid}`] } : {}),
    affiliation: {
      "@type": "EducationalOrganization",
      name: prof.university.name,
      ...(prof.university.officialUrl ? { url: prof.university.officialUrl } : {}),
    },
  };
  const crumbLd = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Professors", item: `${SITE}/professors` },
      { "@type": "ListItem", position: 2, name: prof.university.name, item: `${SITE}/universities/${prof.university.id}` },
      { "@type": "ListItem", position: 3, name: prof.name, item: canonical },
    ],
  };
  const statusLabel =
    prof.dataStatus === "verified"
      ? "Verified"
      : prof.dataStatus === "needs_review"
      ? "Needs review"
      : prof.dataStatus === "outdated"
      ? "Possibly outdated"
      : "Unverified";

  return (
    <div className="page-container py-10">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(personLd) }} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(crumbLd) }} />
      <nav aria-label="Breadcrumb" className="text-sm text-[var(--gray-500)]">
        <Link href="/professors" className="hover:text-[var(--navy)]">Professors</Link>
        <span> / </span>
        <span className="text-[var(--navy)]">{prof.name}</span>
      </nav>

      <div className="mt-4 grid gap-8 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <h1 className="section-title break-words">
            {prof.name}
            {prof.nameZh && (
              <span className="ml-2 text-xl font-normal text-gray-400">{prof.nameZh}</span>
            )}
          </h1>
          <p className="mt-1 text-gray-600">
            {prof.position || "Position not listed"}
            {prof.department ? ` · ${prof.department}` : ""}
          </p>
          <p className="text-gray-500">
            <Link href={`/universities/${prof.university.id}`} className="text-[var(--teal)] hover:underline">
              {prof.university.name}
            </Link>
            {prof.university.city ? ` · ${prof.university.city}` : ""}
          </p>
          {prof.school && <p className="mt-1 text-sm text-gray-500">{prof.school}</p>}

          {(() => {
            const source = sourceDisplay(prof.profileUrl, prof.dataStatus);
            return (
              <section className="mt-4 max-w-xl rounded-lg border border-gray-200 bg-white p-4 text-sm">
                <p className="font-semibold text-[var(--navy)]">Source</p>
                {source.href ? (
                  <a href={source.href} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block break-all text-[var(--teal)] hover:underline">
                    {source.source}
                  </a>
                ) : (
                  <p className="mt-1 text-gray-700">{source.source}</p>
                )}
                <p className="mt-3 font-semibold text-[var(--navy)]">Verification</p>
                <p className={`mt-1 ${source.tone}`}>{source.verification}</p>
              </section>
            );
          })()}

          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="badge-navy">{statusLabel}</span>
            {prof.verifiedAt && (
              <span className="badge-teal">
                Last verified {prof.verifiedAt.toISOString().slice(0, 10)}
              </span>
            )}
          </div>

          <div className="mt-6 flex flex-wrap gap-2">
            {prof.email ? (
              <a href={`mailto:${prof.email}`} className="btn-secondary text-sm break-all">
                {prof.email}
              </a>
            ) : (
              <span className="text-sm text-gray-400">Email not listed</span>
            )}
            {personalProfile ? (
              <a href={prof.profileUrl!} target="_blank" rel="noopener noreferrer" className="btn-ghost text-sm">
                Personal profile →
              </a>
            ) : prof.university.officialUrl ? (
              <a href={prof.university.officialUrl} target="_blank" rel="noopener noreferrer" className="btn-ghost text-sm">
                University website (not a personal profile) →
              </a>
            ) : (
              <span className="text-sm text-gray-400">No personal profile URL on file</span>
            )}
          </div>

          <section className="mt-8">
            <h2 className="text-lg font-semibold text-[var(--navy)]">On file</h2>
            <dl className="mt-3 space-y-2 text-sm text-[var(--gray-700)]">
              <div><dt className="text-xs text-[var(--gray-500)]">Email</dt><dd>{prof.email?.trim() || "Not publicly verified"}</dd></div>
              <div><dt className="text-xs text-[var(--gray-500)]">ORCID</dt><dd>{prof.orcid?.trim() || "Not publicly verified"}</dd></div>
              <div><dt className="text-xs text-[var(--gray-500)]">Lab or group</dt><dd>{prof.lab?.trim() || "Not publicly verified"}</dd></div>
              <div><dt className="text-xs text-[var(--gray-500)]">Supervision</dt><dd>Not publicly verified</dd></div>
              <div><dt className="text-xs text-[var(--gray-500)]">Last updated</dt><dd>{prof.updatedAt.toISOString().slice(0, 10)}</dd></div>
            </dl>
          </section>

          <section className="mt-10">
            <h2 className="text-lg font-bold text-[var(--navy)]">Research areas</h2>
            <div className="mt-3 flex flex-wrap gap-2">
              {prof.researchAreas.length === 0 && (
                <span className="text-sm text-gray-400">Not available</span>
              )}
              {prof.researchAreas.map((r) => (
                <Link
                  key={r.researchAreaId}
                  href={`/professors?area=${encodeURIComponent(r.researchArea.name)}`}
                  className="tag hover:opacity-80"
                >
                  {r.researchArea.name}
                </Link>
              ))}
            </div>
          </section>

          <section className="mt-8">
            <h2 className="text-lg font-bold text-[var(--navy)]">Research interests</h2>
            <p className="mt-2 leading-relaxed text-gray-700">
              {prof.researchInterests || "Not available"}
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-lg font-bold text-[var(--navy)]">Funding</h2>
            <p className="mt-2 text-sm font-semibold text-amber-800">{funding.status}</p>
            <p className="mt-1 text-sm text-gray-700">{funding.text}</p>
          </section>

          <section className="mt-8">
            <h2 className="text-lg font-bold text-[var(--navy)]">Research timeline</h2>
            <p className="mt-2 text-sm text-gray-600">
              {newestYear ? `Newest stored year: ${newestYear}.` : "No publication year is stored, so recent activity cannot be judged."}
              {" "}Topics on file: {prof.researchInterests || "none"}.
            </p>
            {timeline.length === 0 ? (
              <p className="mt-2 text-sm text-gray-600">No publication titles are stored.</p>
            ) : (
              <div className="mt-3 space-y-4">
                {timeline.map((group) => (
                  <div key={group.year}>
                    <p className="text-sm font-semibold text-[var(--navy)]">{group.year}</p>
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-gray-700">
                      {group.titles.map((title) => (
                        <li key={title}>{title}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
            {prof.dataStatus !== "verified" && (
              <p className="mt-2 text-sm text-amber-800">Papers linked only by a name match stay hidden.</p>
            )}
          </section>
        </div>

        <div className="lg:col-span-1">
          <div className="card sticky top-24 p-6">
            <h2 className="text-lg font-bold text-[var(--navy)]">Research match score</h2>
            {!student && (
              <p className="mt-3 text-sm text-gray-600">
                Sign in to see your personalized research match.
              </p>
            )}
            {student && gaps.length > 0 && (
              <p className="mt-3 text-sm text-gray-600">
                Complete your profile to improve this match. Missing: {gaps.join(", ")}.
              </p>
            )}
            {match && !match.incomplete && (
              <div className="mt-4">
                <MatchScore
                  score={match.score}
                  breakdown={pro ? { ...match.breakdown, publicationOverlap: prof.publications?.trim() ? match.breakdown.publicationOverlap : null } : null}
                  reasons={pro ? match.reasons : []}
                />
                {!pro && <p className="mt-3 text-sm text-[var(--gray-700)]">The score is visible. The breakdown is on Pro. <Link href="/pricing" className="text-[var(--teal)]">Go Pro</Link></p>}
              </div>
            )}
            <div className="mt-4 border-t border-gray-100 pt-4">
              <p className="text-sm font-semibold text-[var(--navy)]">Target: {target.level} priority</p>
              <ul className="mt-2 space-y-1 text-sm text-gray-700">
                {target.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
            <div className="mt-4 space-y-2">
              <p className="text-xs text-[var(--gray-500)]">Match, then draft, then copy. Mark Contacted on the tracker. Follow up only after you send it yourself.</p>
              <Link href={student ? "/profile" : "/login"} className="btn-secondary block text-center text-sm">
                {student ? "Edit profile" : "Sign in"}
              </Link>
              <SaveToTrackerButton professorId={prof.id} />
              <EmailGeneratorButton professorId={prof.id} />
              <EmailGeneratorButton professorId={prof.id} kind="follow-up" />
              <ProposalButton professorId={prof.id} endpoint="/api/sop" button="Draft statement of purpose" heading="Statement of purpose" />
              <ProposalButton professorId={prof.id} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
