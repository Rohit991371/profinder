import test from "node:test";
import assert from "node:assert/strict";
import { contentTypeFor, safeDocumentName } from "./apply-storage";

test("safeDocumentName allows supported document formats", () => {
  assert.equal(safeDocumentName("transcript.pdf"), "transcript.pdf");
  assert.equal(safeDocumentName("my CV.docx"), "my_CV.docx");
  assert.equal(safeDocumentName("photo.PNG"), "photo.PNG");
});

test("safeDocumentName rejects unsupported and executable formats", () => {
  assert.equal(safeDocumentName("payload.html"), null);
  assert.equal(safeDocumentName("script.svg"), null);
  assert.equal(safeDocumentName("archive.exe"), null);
});

test("safeDocumentName removes path components and caps long names", () => {
  assert.equal(safeDocumentName("../../transcript.pdf"), "transcript.pdf");
  assert.equal(safeDocumentName("a".repeat(100) + ".pdf")?.length, 80);
});

test("contentTypeFor returns safe download MIME types", () => {
  assert.equal(contentTypeFor("report.pdf"), "application/pdf");
  assert.equal(contentTypeFor("unknown.bin"), "application/octet-stream");
});
