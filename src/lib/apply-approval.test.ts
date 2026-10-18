import assert from "node:assert/strict";
import test from "node:test";
import { canMarkSubmitted } from "./apply-approval";

test("submission requires at least one application and approval for every item", () => {
  assert.equal(canMarkSubmitted([]), false);
  assert.equal(canMarkSubmitted(["approved"]), true);
  assert.equal(canMarkSubmitted(["approved", "approved"]), true);
  assert.equal(canMarkSubmitted(["approved", "ready_for_review"]), false);
  assert.equal(canMarkSubmitted(["selected", "approved"]), false);
});
