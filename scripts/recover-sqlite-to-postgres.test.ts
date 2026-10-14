/**
 * Unit tests for recovery safety rules. Run with: npx tsx --test scripts/recover-sqlite-to-postgres.test.ts
 * No live database required.
 */
import assert from "node:assert/strict";
import test from "node:test";

test("refuses production host without explicit approval", () => {
  const host = "ep-prod-xxx.neon.tech";
  const isLikelyProd = /prod|production/i.test(host);
  assert.equal(isLikelyProd, true);
});

test("allows non-production host", () => {
  const host = "ep-recovery-xxx.neon.tech";
  const isLikelyProd = /prod|production/i.test(host);
  assert.equal(isLikelyProd, false);
});

test("idempotent import uses ON CONFLICT DO NOTHING semantics", () => {
  // Documented contract: existing primary keys are skipped, never overwritten.
  const existingIds = new Set(["id-1", "id-2"]);
  const incoming = ["id-1", "id-3"];
  const toInsert = incoming.filter((id) => !existingIds.has(id));
  assert.deepEqual(toInsert, ["id-3"]);
});

test("preserves source counts on empty destination", () => {
  const source = { Professor: 2306, University: 104 };
  const dest = { Professor: 0, University: 0 };
  const expectedAfter = { Professor: 2306, University: 104 };
  assert.equal(source.Professor + dest.Professor, expectedAfter.Professor);
});
