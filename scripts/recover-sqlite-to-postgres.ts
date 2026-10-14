/**
 * Idempotent, non-destructive SQLite → PostgreSQL recovery.
 * Preserves primary keys. ON CONFLICT DO NOTHING. Never truncates or overwrites.
 *
 * Preflight only (safe):  npx tsx scripts/recover-sqlite-to-postgres.ts --preflight
 * Dry-run:                npx tsx scripts/recover-sqlite-to-postgres.ts --dry-run
 * Execute (staging only): CONFIRM_RECOVERY=preserve-ids npx tsx scripts/recover-sqlite-to-postgres.ts
 *
 * Requires DATABASE_URL (postgres, non-production unless ALLOW_PRODUCTION=explicitly-approved)
 * and SQLITE_PATH (default prisma/dev.db).
 */
import { PrismaClient } from "@prisma/client";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";

const TABLES = [
  "University",
  "ResearchArea",
  "Program",
  "Professor",
  "Scholarship",
  "Publication",
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
print('INTEGRITY:' + c.execute('PRAGMA integrity_check').fetchone()[0], file=__import__('sys').stderr)
`;
  const out = execSync(`python3 -c ${JSON.stringify(py)}`, { encoding: "utf8" });
  return JSON.parse(out);
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
  const preflight = args.includes("--preflight") || args.includes("--dry-run");
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

  const host = new URL(dbUrl).hostname;
  const isLikelyProd = /prod|production/i.test(host) || /prod|production/i.test(dbUrl);
  console.log(`Destination host: ${host}`);
  console.log(`Likely production: ${isLikelyProd}`);
  if (isLikelyProd && !allowProd) {
    console.error("Refusing production target. Set ALLOW_PRODUCTION=explicitly-approved only after manual approval.");
    process.exit(1);
  }

  const src = sqliteCounts(sqlitePath);
  const dst = await pgCounts();
  console.log("Source (SQLite):", src);
  console.log("Destination (Postgres):", dst);

  if (dryRun || !confirm) {
    console.log("DRY-RUN / PREFLIGHT ONLY — no writes.");
    if (!confirm) console.log("To execute on an approved staging branch: CONFIRM_RECOVERY=preserve-ids");
    await prisma.$disconnect();
    return;
  }

  console.error("Full row-level import is implemented in scripts/migrate-sqlite-to-postgres.py (ON CONFLICT DO NOTHING, preserves IDs).");
  console.error("Run that script with CONFIRM_MIGRATE=preserve-ids after this preflight passes on a staging branch.");
  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
