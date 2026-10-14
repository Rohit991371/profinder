/**
 * Resumable multi-source academic candidate collector (OpenAlex first).
 * Stores only UNVERIFIED candidates. Never auto-verifies.
 * Safe to re-run. Checkpointed. Rate-limited.
 *
 * Dry-run: npx tsx scripts/collect-openalex-candidates.ts --country CN --max 500 --dry-run
 * Write (non-production DATABASE_URL only): CONFIRM_COLLECT=yes npx tsx ...
 */
import { PrismaClient } from "@prisma/client";
import fs from "fs";
import path from "path";

const prisma = new PrismaClient();
const CHECKPOINT = path.resolve("data/checkpoints/openalex-authors.json");
const OPENALEX = "https://api.openalex.org/authors";
const RATE_MS = 150;

interface Args { country?: string; max: number; dryRun: boolean; }

function parseArgs(): Args {
  const a = process.argv.slice(2);
  const country = a.includes("--country") ? a[a.indexOf("--country") + 1] : undefined;
  const max = a.includes("--max") ? parseInt(a[a.indexOf("--max") + 1], 10) : 1000;
  return { country, max: Number.isFinite(max) ? max : 1000, dryRun: a.includes("--dry-run") };
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

async function main() {
  const { country, max, dryRun } = parseArgs();
  if (!dryRun && process.env.CONFIRM_COLLECT !== "yes") {
    console.error("Refusing write. Use --dry-run or CONFIRM_COLLECT=yes on a non-production branch.");
    process.exit(1);
  }

  let cursor = "*";
  let processed = 0;
  if (fs.existsSync(CHECKPOINT)) {
    const ck = JSON.parse(fs.readFileSync(CHECKPOINT, "utf8"));
    cursor = ck.cursor || "*";
    processed = ck.processed || 0;
  }

  console.log(`Starting: max=${max} country=${country||"any"} dryRun=${dryRun} processed=${processed}`);

  while (processed < max) {
    const params = new URLSearchParams({ "per-page": "100", cursor, select: "id,display_name,orcid,works_count,last_known_institutions" });
    if (country) params.set("filter", `last_known_institutions.country_code:${country}`);

    const res = await fetch(`${OPENALEX}?${params}`);
    if (!res.ok) { console.error("OpenAlex", res.status); break; }
    const data = await res.json();
    const results = data.results || [];
    if (!results.length) break;

    for (const author of results) {
      if (processed >= max) break;
      const openAlexId = author.id;
      if (!openAlexId) continue;

      if (!dryRun) {
        const exists = await prisma.professor.findFirst({ where: { openAlexId }, select: { id: true } });
        if (exists) { processed++; continue; }
        // Safe log-only until university linking and staging table are confirmed on target branch
        console.log(`[candidate] ${author.display_name} ${openAlexId}`);
      }
      processed++;
    }

    cursor = data.meta?.next_cursor;
    if (!cursor) break;

    fs.mkdirSync(path.dirname(CHECKPOINT), { recursive: true });
    fs.writeFileSync(CHECKPOINT, JSON.stringify({ cursor, processed, at: new Date().toISOString() }, null, 2));
    await sleep(RATE_MS);
  }

  console.log(`Finished. Processed: ${processed}`);
}

main().catch(console.error).finally(() => prisma.$disconnect());
