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
  const configured = process.env.DATABASE_URL || "";
  // Real Postgres / external URL — use as-is
  if (configured && !configured.startsWith("file:")) {
    return configured;
  }

  // Local development: allow file: SQLite
  if (!process.env.VERCEL) {
    const bundled = resolveBundledDb();
    return configured || (bundled ? `file:${bundled}` : "file:./prisma/dev.db");
  }

  // Vercel production WITHOUT a real DATABASE_URL is broken for auth.
  // Ephemeral /tmp SQLite loses every password reset and signup on cold start.
  if (process.env.VERCEL && process.env.VERCEL_ENV === "production") {
    throw new Error(
      "DATABASE_URL must be set to a persistent PostgreSQL connection string in Vercel Production. " +
        "SQLite on /tmp is ephemeral and breaks login after password reset."
    );
  }

  // Preview on Vercel: still allow bundled copy for non-auth demos
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
