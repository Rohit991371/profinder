import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_ADMIN_PAGE_SIZE, MAX_ADMIN_PAGE_SIZE, parseAdminPagination } from "./admin-pagination";

test("uses safe defaults when pagination parameters are absent", () => {
  assert.deepEqual(parseAdminPagination(new URLSearchParams()), {
    page: 1,
    pageSize: DEFAULT_ADMIN_PAGE_SIZE,
  });
});

test("clamps page and page size to safe positive values", () => {
  assert.deepEqual(parseAdminPagination(new URLSearchParams("page=-3&pageSize=0")), {
    page: 1,
    pageSize: 1,
  });
});

test("caps page size and preserves valid page values", () => {
  assert.deepEqual(parseAdminPagination(new URLSearchParams("page=4&pageSize=500")), {
    page: 4,
    pageSize: MAX_ADMIN_PAGE_SIZE,
  });
});

test("falls back for malformed pagination values", () => {
  assert.deepEqual(parseAdminPagination(new URLSearchParams("page=nope&pageSize=oops")), {
    page: 1,
    pageSize: DEFAULT_ADMIN_PAGE_SIZE,
  });
});
