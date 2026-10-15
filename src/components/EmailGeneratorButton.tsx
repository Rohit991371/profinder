"use client";

import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function EmailGeneratorButton({
  professorId,
  kind = "email",
}: {
  professorId: string;
  kind?: "email" | "follow-up";
}) {
  const { data: session } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [facts, setFacts] = useState<string[]>([]);
  const [tone, setTone] = useState("professional");
  const [purpose, setPurpose] = useState("supervision");
  const [source, setSource] = useState("");

  async function generate(regenerate = false) {
    if (!session) {
      router.push(`/login?callbackUrl=/professors/${professorId}`);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(kind === "follow-up" ? "/api/follow-up" : "/api/email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ professorId, tone, purpose, regenerate }),
      });
      const data = await res.json();
      if (res.status === 403) {
        setError(data.error || "Pro required");
        return;
      }
      if (!res.ok) throw new Error(data.error || "Failed");
      setSubject(data.subject);
      setBody(data.body);
      setFacts(Array.isArray(data.facts) ? data.facts : []);
      setSource(data.source || "template");
      setOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to generate");
    } finally {
      setLoading(false);
    }
  }

  function copyAll() {
    navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
  }

  return (
    <div>
      <button
        onClick={() => generate(false)}
        disabled={loading}
        className="btn-secondary w-full text-sm"
      >
        {loading ? "Generating..." : kind === "follow-up" ? "Follow-up email (Pro)" : "Generate Email (Pro)"}
      </button>
      {error && (
        <p className="mt-2 text-center text-xs text-amber-700">
          {error}{" "}
          {error.toLowerCase().includes("pro") && (
            <a href="/pricing" className="text-[var(--teal)] hover:underline">
              Upgrade
            </a>
          )}
        </p>
      )}

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card max-h-[90vh] w-full max-w-lg overflow-y-auto p-6">
            <h3 className="text-lg font-bold text-[var(--navy)]">
              {kind === "follow-up" ? "Follow-up draft" : "Outreach email draft"}
            </h3>
            <p className="mt-1 text-xs text-gray-500">
              Review and edit before sending. Source: {source}. We never send email automatically or invent facts.
            </p>
            {facts.length > 0 && (
              <ul className="mt-3 space-y-1 rounded-lg bg-gray-50 p-3 text-xs text-gray-700">
                {facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>
            )}

            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <label className="flex items-center gap-1">
                Tone
                <select className="input text-xs" value={tone} onChange={(e) => setTone(e.target.value)}>
                  <option value="professional">Professional</option>
                  <option value="concise">Concise</option>
                  <option value="enthusiastic">Enthusiastic</option>
                </select>
              </label>
              <label className="flex items-center gap-1">
                Purpose
                <select className="input text-xs" value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                  <option value="supervision">Research supervision</option>
                  <option value="masters">Master’s opportunity</option>
                  <option value="phd">PhD position</option>
                  <option value="funded">Funded / scholarship</option>
                </select>
              </label>
            </div>

            <label className="mt-4 mb-1 block text-xs font-medium text-gray-600">
              Subject
            </label>
            <input
              className="input"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />

            <label className="mt-3 mb-1 block text-xs font-medium text-gray-600">
              Body
            </label>
            <textarea
              className="input min-h-[220px] font-mono text-xs"
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />

            <div className="mt-4 flex flex-wrap gap-2">
              <button onClick={() => generate(true)} disabled={loading} className="btn-secondary text-sm">
                {loading ? "..." : "Regenerate"}
              </button>
              <button onClick={copyAll} className="btn-primary text-sm">Copy</button>
              <a className="btn-secondary text-sm" href={`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`}>Open email app</a>
              <button
                onClick={() => setOpen(false)}
                className="btn-secondary text-sm"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
