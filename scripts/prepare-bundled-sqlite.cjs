/**
 * Align the bundled SQLite database with prisma/schema.prisma before building.
 *
 * This is intentionally fail-closed: it never uses --accept-data-loss. If Prisma
 * detects a change that could delete data, the Vercel build must fail for review
 * instead of silently resetting or truncating the bundled database.
 */
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const databaseFile = path.resolve("prisma", "dev.db");
if (!fs.existsSync(databaseFile)) {
  console.error("Bundled SQLite database is missing: prisma/dev.db");
  process.exit(1);
}

const cli = process.platform === "win32" ? "npx.cmd" : "npx";
const result = spawnSync(
  cli,
  ["prisma", "db", "push", "--schema", "prisma/schema.prisma", "--skip-generate"],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      // Prisma resolves this path relative to prisma/schema.prisma.
      // Do not use the Vercel DATABASE_URL here: production is currently using
      // the bundled SQLite database through src/lib/prisma.ts.
      DATABASE_URL: "file:./dev.db",
    },
  }
);

if (result.error) {
  console.error("Could not run Prisma schema sync:", result.error.message);
  process.exit(1);
}
if (result.status !== 0) {
  console.error("Bundled SQLite schema sync failed; refusing to continue the build.");
  process.exit(result.status || 1);
}
