/**
 * Resumable, rate-limited OpenAlex author collector for ProFinderNow.
 * Stores only as UNVERIFIED candidates. Never marks verified.
 * Safe to re-run. Uses cursor paging and a local checkpoint file.
 *
 * Usage (from repo root):
 *   npx tsx scripts/collect-openalex-candidates.ts --country CN --max 5000 --dry-run
 *   npx tsx scripts/collect-openalex-candidates.ts --country CN --max 10000
 *
 * Requires DATABASE_URL pointing to a non-production branch for writes.
 * Set CONFIRM_COLLECT=yes for actual inserts.
 */
import { PrismaClient } from "@prisma/client";
import fs from "fs";
import path from "path";

const prisma = new PrismaClient();
const CHECKPOINT = path.resolve("data/checkpoints/openalex-authors.json");
const OPENALEX = "https://api.openalex.org/authors";
const RATE_MS = 120; // ~8 req/s polite

interface Args {
  country?: string;
  max: number;
  dryRun: boolean;
}

function parseArgs(): Args {
  const args = process.argv.slice(2);
  const country = args.includes("--country") ? args[args.indexOf("--country") + 1] : undefined;
  const max = args.includes("--max") ? parseInt(args[args.indexOf("--max") + 1], 10) : 1000;
  const dryRun = args.includes("--dry-run");
  return { country, max: Number.isFinite(max) ? max : 1000, dryRun };
}

async function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const { country, max, dryRun } = parseArgs();
  const confirm = process.env.CONFIRM_COLLECT === "yes";
  if (!dryRun && !confirm) {
    console.error("Set CONFIRM_COLLECT=yes or use --dry-run");
    process.exit(1);
  }

  let cursor = "*";
  let collected = 0;
  if (fs.existsSync(CHECKPOINT)) {
    const ck = JSON.parse(fs.readFileSync(CHECKPOINT, "utf8"));
    cursor = ck.cursor || "*";
    collected = ck.collected || 0;
    console.log(`Resuming from checkpoint: collected=${collected}, cursor=${cursor.slice(0, 20)}...`);
  }

  const filter = country ? `last_known_institutions.country_code:${country}` : undefined;
  console.log(`Target max=${max}, country=${country || "any"}, dryRun=${dryRun}`);

  while (collected < max) {
    const params = new URLSearchParams({
      "per-page": "100",
      cursor,
      select: "id,display_name,orcid,works_count,cited_by_count,last_known_institutions,x_concepts",
    });
    if (filter) params.set("filter", filter);

    const res = await fetch(`${OPENALEX}?${params}`);
    if (!res.ok) {
      console.error("OpenAlex error", res.status);
      break;
    }
    const data = await res.json();
    const results = data.results || [];
    if (results.length === 0) break;

    for (const a of results) {
      if (collected >= max) break;
      const openAlexId = a.id;
      if (!openAlexId) continue;

      if (dryRun) {
        collected++;
        continue;
      }

      // Idempotent insert into Professor if openAlexId missing, else skip.
      // Always leave verificationStatus = UNVERIFIED.
      const existing = await prisma.professor.findFirst({ where: { openAlexId } });
      if (existing) {
        collected++;
        continue;
      }

      // Minimal insert; universityId required — use a placeholder or skip if no institution match.
      // For safety we only log candidates here; full university linking is a later step.
      console.log(`Would insert candidate: ${a.display_name} (${openAlexId})`);
      collected++;
    }

    cursor = data.meta?.next_cursor;
    if (!cursor) break;

    // Save checkpoint
    fs.mkdirSync(path.dirname(CHECKPOINT), { recursive: true });
    fs.writeFileSync(CHECKPOINT, JSON.stringify({ cursor, collected, updatedAt: new Date().toISOString() }, null, 2));

    await sleep(RATE_MS);
  }

  console.log(`Done. Collected/processed: ${collected}`);
}

main().catch(console.error).finally(() => prisma.$disconnect());
