/**
 * Idempotent, non-destructive SQLite → PostgreSQL recovery preflight.
 * Full row import remains in scripts/migrate-sqlite-to-postgres.py (ON CONFLICT DO NOTHING).
 *
 * npx tsx scripts/recover-sqlite-to-postgres.ts --preflight
 * npx tsx scripts/recover-sqlite-to-postgres.ts --dry-run
 *
 * Never prints DATABASE_URL. Refuses production hosts unless ALLOW_PRODUCTION=explicitly-approved.
 */
import { PrismaClient } from "@prisma/client";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";

const TABLES = [
  "University", "ResearchArea", "Topic", "Program", "Professor", "Scholarship",
  "Publication", "ProfessorSource", "ProfessorResearchArea", "ProfessorTopic",
  "Student", "Application",
] as const;

const prisma = new PrismaClient();

function sqliteCounts(sqlitePath: string): Record<string, number> {
  const py = `
import sqlite3, json
c = sqlite3.connect(${JSON.stringify(sqlitePath)})
out = {}
for t in ${JSON.stringify(TABLES)}:
    try:
        out[t] = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    except Exception:
        out[t] = -1
print(json.dumps(out))
`;
  return JSON.parse(execSync(`python3 -c ${JSON.stringify(py)}`, { encoding: "utf8" }));
}

async function pgCounts(): Promise<Record<string, number>> {
  const result: Record<string, number> = {};
  for (const t of TABLES) {
    try {
      const rows = await prisma.$queryRawUnsafe<[{ count: bigint }]>(`SELECT COUNT(*)::bigint AS count FROM "${t}"`);
      result[t] = Number(rows[0]?.count ?? 0);
    } catch {
      result[t] = -1;
    }
  }
  return result;
}

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes("--dry-run") || args.includes("--preflight");
  const confirm = process.env.CONFIRM_RECOVERY === "preserve-ids";
  const allowProd = process.env.ALLOW_PRODUCTION === "explicitly-approved";

  const sqlitePath = path.resolve(process.env.SQLITE_PATH || "prisma/dev.db");
  const dbUrl = process.env.DATABASE_URL || "";

  console.log("=== Recovery preflight ===");
  if (!fs.existsSync(sqlitePath)) {
    console.error("SQLite file missing:", sqlitePath);
    process.exit(1);
  }
  if (!dbUrl.startsWith("postgres")) {
    console.error("DATABASE_URL must be a PostgreSQL connection string. Value not printed.");
    process.exit(1);
  }

  let host = "unknown";
  try { host = new URL(dbUrl).hostname; } catch {}
  const isLikelyProd = /prod|production/i.test(host);
  console.log(`Destination host: ${host}`);
  console.log(`Likely production: ${isLikelyProd}`);
  if (isLikelyProd && !allowProd) {
    console.error("Refusing production-like host. Set ALLOW_PRODUCTION=explicitly-approved only after manual review.");
    process.exit(1);
  }

  const src = sqliteCounts(sqlitePath);
  const dst = await pgCounts();
  console.log("Source (SQLite):", src);
  console.log("Destination (Postgres):", dst);

  const missing = TABLES.filter((t) => dst[t] === -1);
  if (missing.length) {
    console.error("Destination missing tables (schema drift):", missing.join(", "));
    console.error("Apply Prisma migrations to the recovery branch first. No data written.");
    process.exit(1);
  }

  if (dryRun || !confirm) {
    console.log("DRY-RUN / PREFLIGHT ONLY — no writes.");
    console.log("To import: CONFIRM_MIGRATE=preserve-ids python scripts/migrate-sqlite-to-postgres.py");
    await prisma.$disconnect();
    return;
  }

  console.error("Row-level import is in scripts/migrate-sqlite-to-postgres.py.");
  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
