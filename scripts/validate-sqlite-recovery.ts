/**
 * Read-only validator for the original SQLite recovery file.
 * Usage: npx tsx scripts/validate-sqlite-recovery.ts
 * Does not connect to Neon or write anything.
 */
import { execSync } from "child_process";
import fs from "fs";
import path from "path";

const DB = path.resolve("prisma/dev.db");

function main() {
  if (!fs.existsSync(DB)) {
    console.error("prisma/dev.db not found");
    process.exit(1);
  }
  const size = fs.statSync(DB).size;
  console.log(`File size: ${size} bytes`);

  try {
    const out = execSync(`python3 -c "
import sqlite3
c = sqlite3.connect('${DB.replace(/\\/g, '/')}')
for t in ['University','Program','Professor','Publication','Scholarship','ResearchArea']:
    print(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
print('integrity', c.execute('PRAGMA integrity_check').fetchone()[0])
"`, { encoding: "utf8" });
    console.log(out);
  } catch (e) {
    console.error("Validation failed:", (e as Error).message);
    process.exit(1);
  }
  console.log("Validation complete. No writes performed.");
}

main();
