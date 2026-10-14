/**
 * Read-only local validation of prisma/dev.db (and optional backup).
 * No database connection required. No secrets.
 * Usage: npx tsx scripts/validate-sqlite-recovery.ts
 */
import { execSync } from "child_process";
import fs from "fs";
import path from "path";

const TABLES = [
  "University", "Program", "Professor", "Publication", "Scholarship", "ResearchArea",
  "ProfessorResearchArea", "ProfessorSource", "ProfessorTopic", "Topic", "Student", "Application",
];

function check(dbPath: string) {
  if (!fs.existsSync(dbPath)) {
    console.log(`MISSING: ${dbPath}`);
    return;
  }
  const size = fs.statSync(dbPath).size;
  console.log(`\n=== ${dbPath} (${size} bytes) ===`);
  const py = `
import sqlite3, json
c = sqlite3.connect(${JSON.stringify(dbPath)})
out = {}
for t in ${JSON.stringify(TABLES)}:
    try:
        out[t] = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    except Exception:
        out[t] = None
print(json.dumps(out, indent=2))
print("integrity:" + c.execute("PRAGMA integrity_check").fetchone()[0])
`;
  try {
    console.log(execSync(`python3 -c ${JSON.stringify(py)}`, { encoding: "utf8" }));
  } catch (e) {
    console.error("Failed:", (e as Error).message);
  }
}

check(path.resolve("prisma/dev.db"));
check(path.resolve("prisma/dev-recovery-backup-20261014.db"));
console.log("\nLocal validation complete. No writes.");
