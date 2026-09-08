import assert from "node:assert/strict";
import test from "node:test";
import renderer from "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_preview_renderer.js";
import preview from "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_preview.js";

const HV = "https://hyperview.org/hyperview";
function xml(name, attrs = {}, children = [], namespaceURI = HV) {
  return {nodeType: 1, localName: name, namespaceURI,
    attributes: Object.entries(attrs).map(([name, value]) => ({name, value, namespaceURI: null})),
    childNodes: children.map(child => typeof child === "string" ? {nodeType: 3, textContent: child} : child),
    getAttribute(name) { return attrs[name] ?? null; }};
}
function documentStub() {
  function node(tag, text = "") {
    return {tag, textContent: text, children: [], attrs: {}, style: {},
      appendChild(child) { this.children.push(child); return child; },
      setAttribute(key, value) { this.attrs[key] = value; }};
  }
  return {createElement: tag => node(tag), createTextNode: text => node("#text", text)};
}
const serialize = value => JSON.stringify(value, (key, value) => typeof value === "function" ? undefined : value);

test("style allowlist supports known dimensions, flex and spacing only", () => {
  const warnings = new Set();
  const styles = renderer.styleDeclarations(xml("style", {id: "title", width: "90%", marginHorizontal: "auto", padding: "8", backgroundColor: "#abc", fontSize: "18", flexDirection: "row", position: "absolute", backgroundImage: "url(https://bad.test)"}), warnings);
  assert.equal(styles.width, "90%");
  assert.equal(styles["margin-left"], "auto");
  assert.equal(styles.padding, "8px");
  assert.equal(styles["font-size"], "18px");
  assert.equal(styles["flex-direction"], "row");
  assert.equal(styles.position, undefined);
  assert.equal(styles["background-image"], undefined);
  assert.ok(warnings.size >= 2);
});

test("style values cannot inject CSS URLs, declarations, variables or host selectors", () => {
  for (const value of ["url(https://bad.test)", "red;position:fixed", "var(--secret)", "expression(alert(1))", "red</style><script>"]) {
    const styles = renderer.styleDeclarations(xml("style", {backgroundColor: value, width: value, fontFamily: value}), new Set());
    assert.equal(Object.keys(styles).length, 0);
  }
});

test("renderer creates inert known DOM, keeps text, never forwards identifiers or behavior", () => {
  const root = xml("screen", {}, [xml("body", {}, [xml("text", {id: "admin-login", href: "https://bad.test", style: "headline"}, ["<script>alert(1)</script>"]), xml("behavior", {action: "replace", href: "https://bad.test"})]), xml("styles", {}, [xml("style", {id: "headline", color: "blue"})])]);
  const result = renderer.renderTree(root, documentStub());
  const output = serialize(result.node);
  assert.ok(output.includes("<script>alert(1)</script>"));
  assert.ok(output.includes('"color":"blue"'));
  assert.ok(!output.includes("admin-login"));
  assert.ok(!output.includes("https://bad.test"));
  assert.ok(result.warnings.some(message => message.includes("behavior")));
});

test("media and custom/native components use placeholders without resource attributes", () => {
  const root = xml("view", {}, [xml("image", {source: "https://secret.test/image"}), xml("web-view", {url: "https://secret.test"}), xml("camera", {}, [], "urn:custom")]);
  const result = renderer.renderTree(root, documentStub());
  assert.ok(!serialize(result.node).includes("https://secret.test"));
  assert.ok(result.warnings.some(message => message.includes("image")));
  assert.ok(result.warnings.some(message => message.includes("camera")));
  assert.equal(result.node.children.length, 3);
});

test("screen selector and styles stay local to selected screen", () => {
  const screen = color => xml("screen", {id: color}, [xml("styles", {}, [xml("style", {id: "text", color})]), xml("body", {}, [xml("text", {style: "text"}, [color])])]);
  const root = xml("doc", {}, [screen("red"), screen("blue")]);
  const result = renderer.renderTree(root, documentStub(), 1);
  assert.deepEqual(result.screens, ["red", "blue"]);
  assert.ok(serialize(result.node).includes('"color":"blue"'));
  assert.ok(!serialize(result.node).includes('"color":"red"'));
});

test("document-level styles apply to the selected screen", () => {
  const root = xml("doc", {}, [
    xml("styles", {}, [xml("style", {id: "headline", color: "blue"})]),
    xml("screen", {id: "main"}, [
      xml("body", {}, [xml("text", {style: "headline"}, ["Styled"])]),
    ]),
  ]);
  const result = renderer.renderTree(root, documentStub());
  assert.ok(serialize(result.node).includes('"color":"blue"'));
  assert.ok(!result.warnings.some(message => message.includes("missing")));
});

test("client rejects unbounded deep XML trees", () => {
  let root = xml("text", {}, ["deep"]);
  for (let count = 0; count < 150; count += 1) root = xml("view", {}, [root]);
  assert.throws(() => renderer.renderTree(root, documentStub()), /complex/i);
});

function harness() {
  let source = {name: "draft.xml", content: "<view/>", scenario: "empty"};
  const pending = [], states = [], results = [];
  const controller = preview.createController({read: () => ({...source}),
    send: (data, signal) => new Promise((resolve, reject) => pending.push({data, signal, resolve, reject})),
    onState: state => states.push(state), onResult: result => results.push(result)});
  return {controller, pending, states, results, edit: value => { source = {...source, ...value}; controller.changed(); }};
}

test("preview reads the original draft once and reports valid without mutating it", async () => {
  const h = harness(); const done = h.controller.refresh();
  assert.deepEqual(h.pending[0].data, {name: "draft.xml", content: "<view/>", scenario: "empty"});
  h.pending[0].resolve({ok: true, hxml: "<view/>", diagnostics: []}); await done;
  assert.deepEqual(h.states, ["loading", "valid"]);
  assert.equal(h.results.length, 1);
});

test("new preview cancels earlier work and drops out-of-order results", async () => {
  const h = harness(); const first = h.controller.refresh(); const second = h.controller.refresh();
  assert.equal(h.pending[0].signal.aborted, true);
  h.pending[1].resolve({ok: true, hxml: "new", diagnostics: []}); await second;
  h.pending[0].resolve({ok: true, hxml: "old", diagnostics: []}); await first;
  assert.deepEqual(h.results.map(result => result.hxml), ["new"]);
});

for (const change of [{content: "changed"}, {name: "renamed.xml"}, {scenario: "other"}]) {
  test("editing invalidates pending result: " + Object.keys(change)[0], async () => {
    const h = harness(); const done = h.controller.refresh(); h.edit(change);
    assert.equal(h.pending[0].signal.aborted, true);
    h.pending[0].resolve({ok: true, hxml: "old", diagnostics: []}); await done;
    assert.equal(h.results.length, 0); assert.equal(h.states.at(-1), "stale");
  });
}

test("diagnostic severities select warning/error states and failures are safe", async () => {
  for (const [result, expected] of [[{ok: true, hxml: "<view/>", diagnostics: [{severity: "warning"}]}, "warning"], [{ok: false, hxml: null, diagnostics: [{severity: "error"}]}, "error"]]) {
    const h = harness(); const done = h.controller.refresh(); h.pending[0].resolve(result); await done;
    assert.equal(h.states.at(-1), expected);
  }
  const h = harness(); const done = h.controller.refresh(); h.pending[0].reject(new Error("secret path")); await done;
  assert.equal(h.states.at(-1), "error"); assert.ok(!serialize(h.results).includes("secret path"));
});

test("source jumps require current draft source coordinates, never rendered/include coordinates", () => {
  const diagnostic = {coordinate_space: "source", template: "draft.xml", line: 3, column: 2};
  assert.deepEqual(preview.sourceLocation(diagnostic, "draft.xml"), {line: 3, column: 1});
  for (const changes of [{coordinate_space: "rendered"}, {template: "include.xml"}, {line: null}, {line: -1}]) {
    assert.equal(preview.sourceLocation({...diagnostic, ...changes}, "draft.xml"), null);
  }
});

test("rendered navigation bounds offsets to the readonly HXML, not source", () => {
  assert.deepEqual(preview.renderedLocation({coordinate_space: "rendered", line: 2}, "one\ntwo\nthree"), {line: 2, start: 4, end: 7});
  assert.equal(preview.renderedLocation({coordinate_space: "source", line: 2}, "one\ntwo"), null);
  assert.equal(preview.renderedLocation({coordinate_space: "rendered", line: 9}, "one\ntwo"), null);
});

test("readonly Admin name field uses escaped server-provided draft name", () => {
  const textarea = {dataset: {hyperviewTemplateName: 'draft"&.xml'}};
  const editor = {getValue: () => "untouched"};
  assert.deepEqual(preview.readDraft(textarea, editor, null, {value: "empty"}), {name: 'draft"&.xml', content: "untouched", scenario: "empty"});
  assert.equal(preview.readDraft(textarea, editor, {value: "renamed.xml"}, {value: "empty"}).name, "renamed.xml");
});

test("identity changes without input events end loading as stale", async () => {
  let source = {name: "draft.xml", content: "old", scenario: "empty"};
  const states = [], results = []; let resolve;
  const controller = preview.createController({read: () => ({...source}), send: () => new Promise(callback => {resolve = callback;}), onState: state => states.push(state), onResult: result => results.push(result)});
  const done = controller.refresh(); source.content = "new";
  resolve({ok: true, hxml: "old", diagnostics: []}); await done;
  assert.deepEqual(states, ["loading", "stale"]); assert.equal(results.length, 0);
});

test("native-like border baseline makes an explicit border width visible", () => {
  const root = xml("screen", {}, [xml("styles", {}, [xml("style", {id: "box", borderWidth: "2", borderColor: "red"})]), xml("body", {}, [xml("view", {style: "box"})])]);
  const output = serialize(renderer.renderTree(root, documentStub()).node);
  assert.ok(output.includes('"border-width":"2px"'));
  assert.ok(output.includes('"border-style":"solid"'));
});
