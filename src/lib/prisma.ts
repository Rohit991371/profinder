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
  // Force SQLite on Vercel using the bundled file. Ignore any Neon/Postgres URL.
  if (process.env.VERCEL) {
    const bundled = resolveBundledDb();
    const target = "/tmp/profinder.db";
    try {
      if (!fs.existsSync(target) && bundled) {
        fs.copyFileSync(bundled, target);
      }
    } catch (e) {
      console.error("Failed to copy bundled db:", e);
    }
    return `file:${target}`;
  }

  const configured = process.env.DATABASE_URL || "";
  if (configured.startsWith("file:")) return configured;

  const bundled = resolveBundledDb();
  return bundled ? `file:${bundled}` : "file:./prisma/dev.db";
}

// Override before PrismaClient is created
const dbUrl = resolveDatabaseUrl();
process.env.DATABASE_URL = dbUrl;

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    datasources: { db: { url: dbUrl } },
    log: process.env.NODE_ENV === "development" ? ["error", "warn"] : ["error"],
  });

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = prisma;

export function ensureSchema() {
  return Promise.resolve();
}

void ensureSchema().catch((error) => {
  console.error("Schema ensure failed:", error);
});
