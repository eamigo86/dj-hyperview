import assert from "node:assert/strict";
import test from "node:test";

import sourceFacts from "../../docs/javascripts/source-facts.js";

const {newestPublishedRelease} = sourceFacts;

test("documentation selects the most recently published release, not API order", () => {
  const releases = [
    {tag_name: "v0.1.0a9", published_at: "2026-09-07T18:00:00Z"},
    {tag_name: "v0.1.0a11", published_at: "2026-09-08T06:00:00Z"},
    {tag_name: "v0.1.0a10", published_at: "2026-09-07T23:00:00Z"},
  ];

  assert.equal(newestPublishedRelease(releases).tag_name, "v0.1.0a11");
  assert.deepEqual(releases.map((release) => release.tag_name), [
    "v0.1.0a9",
    "v0.1.0a11",
    "v0.1.0a10",
  ]);
});

test("documentation ignores drafts and malformed release records", () => {
  const release = newestPublishedRelease([
    {tag_name: "v9.0.0", published_at: "2026-09-09T00:00:00Z", draft: true},
    {tag_name: "v8.0.0", published_at: "not-a-date"},
    {tag_name: "v0.1.0a11", published_at: "2026-09-08T06:00:00Z"},
    null,
  ]);

  assert.equal(release.tag_name, "v0.1.0a11");
  assert.equal(newestPublishedRelease({}), undefined);
});
