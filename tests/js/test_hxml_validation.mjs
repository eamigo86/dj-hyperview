import assert from "node:assert/strict";
import test from "node:test";

import validationApi from "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_validation.js";

const {
  createController,
  createSavePreflight,
  formatDraft,
  readDraft,
  sourceLocation,
} = validationApi;


test("validation reads the unsaved name and current Ace buffer", () => {
  const editor = {getValue: () => "<view>{{ draft }}</view>"};
  const name = {value: "screens/draft.xml"};

  assert.deepEqual(readDraft(editor, name), {
    name: "screens/draft.xml",
    content: "<view>{{ draft }}</view>",
  });
});


test("combined action formats the Ace buffer before validation reads it", () => {
  let value = '<view><text>Ready</text></view>';
  const writes = [];
  const editor = {
    getValue: () => value,
    setValue: (next, cursor) => {
      value = next;
      writes.push([next, cursor]);
    },
  };

  const result = formatDraft(editor, () => ({
    ok: true,
    value: '<view>\n  <text>Ready</text>\n</view>',
  }));

  assert.equal(result.ok, true);
  assert.deepEqual(writes, [['<view>\n  <text>Ready</text>\n</view>', -1]]);
  assert.equal(readDraft(editor, {value: "screen.xml"}).content, result.value);
});


test("combined action still validates unchanged source when formatting is unsafe", () => {
  const source = '<view><text>{{ label }}</view>';
  const writes = [];
  const editor = {
    getValue: () => source,
    setValue: (...args) => writes.push(args),
  };

  const result = formatDraft(editor, () => ({ok: false, value: source}));

  assert.deepEqual(result, {ok: false, value: source, changed: false});
  assert.deepEqual(writes, []);
  assert.equal(readDraft(editor, {value: "screen.xml"}).content, source);
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

  const result = await controller.validate();

  assert.deepEqual(states, ["loading", "valid"]);
  assert.deepEqual(results, [response]);
  assert.equal(result, response);
});


test("validation distinguishes an incomplete static schema check", async () => {
  const states = [];
  const response = {
    ok: true,
    diagnostics: [{severity: "warning", code: "schema_static_incomplete"}],
  };
  const controller = createController({
    read: () => ({name: "screen.xml", content: "<view {{ attrs }} />"}),
    send: async () => response,
    onState: (state) => states.push(state),
    onResult: () => {},
  });

  await controller.validate();

  assert.deepEqual(states, ["loading", "partial"]);
});


test("validation returns a fail-closed result when transport is unavailable", async () => {
  const states = [];
  const results = [];
  const controller = createController({
    read: () => ({name: "screen.xml", content: "<view />"}),
    send: async () => { throw new Error("offline"); },
    onState: (state) => states.push(state),
    onResult: (result) => results.push(result),
  });

  const result = await controller.validate();

  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "transport_error");
  assert.deepEqual(states, ["loading", "error"]);
  assert.deepEqual(results, [result]);
});


test("save preflight resumes the exact Django Admin submit action", async () => {
  for (const name of ["_save", "_addanother", "_continue"]) {
    const submitter = {name};
    const resumed = [];
    const preflight = createSavePreflight({
      run: async () => ({ok: true, diagnostics: []}),
      resume: (button) => resumed.push(button),
    });
    let prevented = false;

    await preflight.handleSubmit({
      submitter,
      preventDefault: () => { prevented = true; },
    });

    assert.equal(prevented, true);
    assert.deepEqual(resumed, [submitter]);
  }
});


test("save preflight blocks invalid and unavailable validation", async () => {
  for (const result of [
    {ok: false, diagnostics: [{severity: "error"}]},
    null,
  ]) {
    let resumed = false;
    const preflight = createSavePreflight({
      run: async () => result,
      resume: () => { resumed = true; },
    });

    await preflight.handleSubmit({
      submitter: {name: "_save"},
      preventDefault: () => {},
    });

    assert.equal(resumed, false);
  }
});


test("save preflight allows warning-only validation", async () => {
  let resumed = false;
  const preflight = createSavePreflight({
    run: async () => ({
      ok: true,
      diagnostics: [{severity: "warning"}],
    }),
    resume: () => { resumed = true; },
  });

  await preflight.handleSubmit({
    submitter: {name: "_continue"},
    preventDefault: () => {},
  });

  assert.equal(resumed, true);
});


test("save preflight does not recurse while resuming native submission", async () => {
  let runs = 0;
  let resumed = 0;
  let preflight;
  const submitter = {name: "_save"};
  preflight = createSavePreflight({
    run: async () => {
      runs += 1;
      return {ok: true, diagnostics: []};
    },
    resume: () => {
      resumed += 1;
      void preflight.handleSubmit({
        submitter,
        preventDefault: () => assert.fail("native resume was intercepted"),
      });
    },
  });

  await preflight.handleSubmit({submitter, preventDefault: () => {}});

  assert.equal(runs, 1);
  assert.equal(resumed, 1);
});


test("save preflight ignores unrelated form submit actions", async () => {
  let ran = false;
  const preflight = createSavePreflight({
    run: async () => {
      ran = true;
      return {ok: true, diagnostics: []};
    },
    resume: () => assert.fail("unrelated submit must stay native"),
  });
  let prevented = false;

  await preflight.handleSubmit({
    submitter: {name: "_delete"},
    preventDefault: () => { prevented = true; },
  });

  assert.equal(ran, false);
  assert.equal(prevented, false);
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
