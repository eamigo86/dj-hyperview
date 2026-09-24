import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import vm from "node:vm";

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
    diagnostics: [{severity: "info", code: "schema_static_incomplete"}],
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


test("informational diagnostics allow saving but never override an invalid result", async () => {
  for (const ok of [true, false]) {
    let resumed = false;
    const preflight = createSavePreflight({
      run: async () => ({ok, diagnostics: [
        {severity: "info", code: "schema_static_incomplete"},
        ...(!ok ? [{severity: "error", code: "schema_attribute"}] : []),
      ]}),
      resume: () => { resumed = true; },
    });
    await preflight.handleSubmit({submitter: {name: "_save"}, preventDefault() {}});
    assert.equal(resumed, ok);
  }
});

function installedValidation(diagnostic, ok) {
  const events = {};
  const jumps = [];
  const element = () => ({
    children: [],
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    addEventListener(name, callback) { this[name] = callback; },
  });
  const diagnostics = element();
  const button = element();
  const status = element();
  const formatStatus = element();
  const name = {...element(), value: "screens/draft.xml"};
  const form = {...element(), querySelector: (selector) => selector === '[name="name"]' ? name : {value: "csrf"}};
  const actions = {
    nextElementSibling: diagnostics,
    querySelector: (selector) => ({
      ".djhv-format-validate": button,
      ".djhv-editor-status": formatStatus,
      ".djhv-source-validation-status": status,
    })[selector],
  };
  const textarea = {
    dataset: {hyperviewValidationUrl: "/validate/"},
    previousElementSibling: {editor: {
      getValue: () => "draft",
      getSession: () => ({on() {}}),
      gotoLine: (...args) => jumps.push(args),
      focus: () => jumps.push("focus"),
    }},
    closest: (selector) => selector === "form" ? form : {nextElementSibling: actions},
  };
  const context = {
    AbortController,
    window: {addEventListener: (name, callback) => { events[name] = callback; }},
    document: {createElement: element, querySelectorAll: () => [textarea]},
    fetch: async () => ({json: async () => ({ok, diagnostics: [diagnostic]})}),
  };
  vm.runInNewContext(readFileSync(new URL(
    "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_validation.js", import.meta.url,
  ), "utf8"), context);
  events.load();
  button.click();
  return {diagnostics, status, jumps};
}

for (const severity of ["info", "warning", "error"]) {
  test(`installed Admin renders ${severity} with correct presentation and source navigation`, async () => {
    const {diagnostics, status, jumps} = installedValidation({
      severity,
      code: severity === "info" ? "schema_static_incomplete" : "other",
      message: "Safe <text> message",
      template: "screens/draft.xml",
      coordinate_space: "source",
      line: 3,
      column: 2,
    }, severity !== "error");
    await new Promise(setImmediate);
    const row = diagnostics.children[0];
    assert.equal(row.className, "djhv-source-validation-diagnostic" +
      (severity === "error" ? "" : ` djhv-source-validation-${severity}`));
    assert.equal(row.textContent, ({info: "Info: ", warning: "Warning: ", error: "Error: "})[severity] +
      "Safe <text> message · source line 3");
    assert.equal(row.innerHTML, undefined);
    if (severity === "info") assert.equal(status.textContent, "Source and static checks passed.");
    if (severity === "error") assert.equal(status.textContent, "Template source is invalid.");
    row.children[0].click();
    assert.deepEqual(jumps, [[3, 1, true], "focus"]);
  });
}

test("informational styling uses neutral colors instead of warning or error accents", () => {
  const css = readFileSync(new URL(
    "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_editor.css", import.meta.url,
  ), "utf8");
  assert.match(css, /\.djhv-source-validation-info\s*\{[^}]*border-left-color:\s*transparent;[^}]*color:\s*var\(--body-quiet-color\);/);
});
