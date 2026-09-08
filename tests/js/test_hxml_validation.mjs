import assert from "node:assert/strict";
import test from "node:test";

import validationApi from "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_validation.js";

const {createController, readDraft, sourceLocation} = validationApi;


test("validation reads the unsaved name and current Ace buffer", () => {
  const editor = {getValue: () => "<view>{{ draft }}</view>"};
  const name = {value: "screens/draft.xml"};

  assert.deepEqual(readDraft(editor, name), {
    name: "screens/draft.xml",
    content: "<view>{{ draft }}</view>",
  });
});


test("validation ignores a response made stale by an editor change", async () => {
  let resolveRequest;
  const states = [];
  const results = [];
  const controller = createController({
    read: () => ({name: "screen.xml", content: "draft"}),
    send: () => new Promise((resolve) => { resolveRequest = resolve; }),
    onState: (state) => states.push(state),
    onResult: (result) => results.push(result),
  });

  const pending = controller.validate();
  controller.changed();
  resolveRequest({ok: true, diagnostics: []});
  await pending;

  assert.deepEqual(states, ["loading", "stale"]);
  assert.deepEqual(results, []);
});


test("validation reports a current successful response", async () => {
  const states = [];
  const results = [];
  const response = {ok: true, diagnostics: []};
  const controller = createController({
    read: () => ({name: "screen.xml", content: "<view />"}),
    send: async () => response,
    onState: (state) => states.push(state),
    onResult: (result) => results.push(result),
  });

  await controller.validate();

  assert.deepEqual(states, ["loading", "valid"]);
  assert.deepEqual(results, [response]);
});


test("source coordinates navigate only within the current draft", () => {
  assert.deepEqual(
    sourceLocation(
      {
        coordinate_space: "source",
        template: "screen.xml",
        line: 3,
        column: 5,
      },
      "screen.xml",
    ),
    {line: 3, column: 4},
  );
  assert.equal(
    sourceLocation(
      {coordinate_space: "rendered", template: "screen.xml", line: 3},
      "screen.xml",
    ),
    null,
  );
});
