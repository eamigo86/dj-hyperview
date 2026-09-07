import assert from "node:assert/strict";
import test from "node:test";

import editorApi from "../../src/dj_hyperview/static/dj_hyperview/admin/hxml_editor.js";

const {
  formatHxml,
  getCompletions,
  protectDjango,
  restoreDjango,
  syncEditor,
} = editorApi;

const catalog = {
  schema_version: "0.110.0",
  elements: {
    view: {
      namespace: "https://hyperview.org/hyperview",
      children: ["text", "{https://example.test/app}swipe-row"],
      allows_custom_children: true,
      attributes: {
        id: {required: false, enum: []},
        direction: {required: false, enum: ["row", "column"]},
      },
    },
    text: {
      namespace: "https://hyperview.org/hyperview",
      children: [],
      allows_custom_children: false,
      attributes: {id: {required: false, enum: []}},
    },
    "{https://example.test/app}swipe-row": {
      namespace: "https://example.test/app",
      children: [],
      allows_custom_children: false,
      attributes: {threshold: {required: true, enum: ["long", "short"]}},
    },
  },
};

test("element completions respect parent context and declared prefixes", () => {
  const source = '<view xmlns="https://hyperview.org/hyperview" xmlns:app="https://example.test/app"><';

  assert.deepEqual(getCompletions(catalog, source, source.length), ["app:swipe-row", "text"]);
});

test("attribute completions omit attributes already present", () => {
  const source = '<view xmlns="https://hyperview.org/hyperview" id="main" d';

  assert.deepEqual(getCompletions(catalog, source, source.length), ["direction"]);
});

test("enumeration completions are scoped to the active attribute", () => {
  const source = '<view xmlns="https://hyperview.org/hyperview" direction="c';

  assert.deepEqual(getCompletions(catalog, source, source.length), ["column"]);
});

test("Django syntax protection round trips without mutation", () => {
  const source = '<view id="{{ screen_id }}">{% if ready %}<text>{# note #}Ready</text>{% endif %}</view>';
  const protectedValue = protectDjango(source);

  assert.equal(restoreDjango(protectedValue.source, protectedValue.tokens), source);
  assert.doesNotMatch(protectedValue.source, /[{][{%#]/);
});

test("formatter restores Django syntax and formats safe HXML", () => {
  const source = '<view>{% if ready %}<text>{{ label }}</text>{% endif %}</view>';

  assert.deepEqual(formatHxml(source), {
    ok: true,
    value: '<view>\n  {% if ready %}\n  <text>{{ label }}</text>\n  {% endif %}\n</view>',
  });
});

test("formatter never mutates unsafe or malformed input", () => {
  const source = '<view><text>{{ label }}</view>';

  assert.deepEqual(formatHxml(source), {ok: false, value: source});
});

test("formatter cannot confuse consumer text with protected token markers", () => {
  const source = '<view><text>DJHVTOKEN0 {{ label }}</text></view>';

  const result = formatHxml(source);

  assert.equal(result.ok, true);
  assert.match(result.value, /DJHVTOKEN0 {{ label }}/);
});

test("submit synchronization copies the latest Ace value", () => {
  const textarea = {value: "old"};
  const editor = {getValue: () => "new"};

  syncEditor(textarea, editor);

  assert.equal(textarea.value, "new");
});
