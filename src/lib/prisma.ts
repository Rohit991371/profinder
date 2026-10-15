import fs from "fs";
import path from "path";
import { PrismaClient } from "@prisma/client";

function resolveBundledDb() {
  const candidates = [
    path.join(process.cwd(), "prisma", "dev.db"),
    path.join(process.cwd(), ".next", "server", "prisma", "dev.db"),
    "/var/task/prisma/dev.db",
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function resolveDatabaseUrl() {
  // Temporary: always use the bundled SQLite database on Vercel
  // (Neon is disconnected). Local dev still respects file: DATABASE_URL.
  if (process.env.VERCEL) {
    const bundled = resolveBundledDb();
    const target = "/tmp/profinder.db";
    if (!fs.existsSync(target)) {
      if (!bundled) {
        throw new Error("Bundled prisma/dev.db was not included in the deployment.");
      }
      fs.copyFileSync(bundled, target);
    }
    return `file:${target}`;
  }

  const configured = process.env.DATABASE_URL || "";
  if (configured && configured.startsWith("file:")) return configured;

  const bundled = resolveBundledDb();
  return bundled ? `file:${bundled}` : "file:./prisma/dev.db";
}

process.env.DATABASE_URL = resolveDatabaseUrl();

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient; schemaReady?: Promise<void> };

export const prisma = globalForPrisma.prisma ?? new PrismaClient({
  log: process.env.NODE_ENV === "development" ? ["error", "warn"] : ["error"],
});

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = prisma;

/** SQLite-only schema patches. No-op when DATABASE_URL is Postgres. */
export function ensureSchema() {
  if (!process.env.DATABASE_URL?.startsWith("file:")) return Promise.resolve();
  if (!globalForPrisma.schemaReady) {
    globalForPrisma.schemaReady = Promise.resolve();
  }
  return globalForPrisma.schemaReady;
}

void ensureSchema().catch((error) => {
  console.error("Schema ensure failed:", error);
});
