import assert from "node:assert/strict";
import { test } from "node:test";
import { NextRequest } from "next/server";
import { dynamic, GET, runtime } from "./route";

test("keeps academic search request-time and on the Node.js runtime", () => {
  assert.equal(dynamic, "force-dynamic");
  assert.equal(runtime, "nodejs");
});

test("preserves the empty-results response for short queries without querying the database", async () => {
  const response = await GET(
    new NextRequest("http://localhost/api/academic-search?q=x"),
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { results: [] });
});
