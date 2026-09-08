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

test("formatter preserves blocktranslate bodies from real HXML templates", () => {
  const source = '<view><text>{% blocktranslate %}  Version {{ version }}.  {% endblocktranslate %}</text></view>';

  const first = formatHxml(source);

  assert.equal(first.ok, true);
  assert.match(first.value, /{% blocktranslate %}  Version {{ version }}\.  {% endblocktranslate %}/);
  assert.deepEqual(formatHxml(first.value), first);
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

for (const [name, source, preserved] of [
  ["preformatted spaces", '<view><text preformatted="true">  Hola  </text></view>', '<text preformatted="true">  Hola  </text>'],
  ["ordinary significant spaces", '<view><text>  Hola  </text></view>', '<text>  Hola  </text>'],
  ["nested text", '<view><text>Hello <text>  friend </text> !</text></view>', '<text>Hello <text>  friend </text> !</text>'],
  ["CDATA", '<view><text><![CDATA[  <hello> & {{ value }}  ]]></text></view>', '<text><![CDATA[  <hello> & {{ value }}  ]]></text>'],
  ["custom subtree", '<view xmlns:app="urn:app"><app:label>  <text> x </text> y </app:label></view>', '<app:label>  <text> x </text> y </app:label>'],
  ["unknown unprefixed subtree", '<view><custom>  <view><text>x</text></view> </custom></view>', '<custom>  <view><text>x</text></view> </custom>'],
  ["foreign default namespace", '<view xmlns="urn:custom"><view> <text>x</text> </view></view>', '<view xmlns="urn:custom"><view> <text>x</text> </view></view>'],
  ["xml space preserve", '<view xml:space="preserve"> <view><text>x</text></view> </view>', '<view xml:space="preserve"> <view><text>x</text></view> </view>'],
  ["dynamic preserve attribute", '<view xml:space="{{ mode }}"> <view><text>x</text></view> </view>', '<view xml:space="{{ mode }}"> <view><text>x</text></view> </view>'],
  ["quoted greater-than", '<view id="a > b"><text> x </text></view>', '<view id="a > b">'],
  ["Django quotes inside attributes", '<view id="{{ value|default:"x" }}"><text>x</text></view>', '<view id="{{ value|default:"x" }}">'],
  ["escaped text and old markers", '<view><text>  &lt;DJHVTOKEN0&gt; {{ label }} DJHVTOKEN_0  </text></view>', '<text>  &lt;DJHVTOKEN0&gt; {{ label }} DJHVTOKEN_0  </text>'],
  ["Django comment body", '<view>{% comment %}<invalid attr= {{ incomplete {% if x %}{% endcomment %}<text> x </text></view>', '{% comment %}<invalid attr= {{ incomplete {% if x %}{% endcomment %}'],
  ["Django verbatim body", '<view>{% verbatim %}<text>  x  </text>{{ raw }}{% endverbatim %}</view>', '{% verbatim %}<text>  x  </text>{{ raw }}{% endverbatim %}'],
  ["named verbatim body", '<view>{% verbatim sample %}{% endverbatim %}<raw>{{ {% endverbatim sample %}</view>', '{% verbatim sample %}{% endverbatim %}<raw>{{ {% endverbatim sample %}'],
  ["Django comment in mixed text", '<text>  A {# <hidden> #} B  </text>', '<text>  A {# <hidden> #} B  </text>'],
]) {
  test(`formatter preserves ${name} and is idempotent`, () => {
    const result = formatHxml(source);
    assert.equal(result.ok, true);
    assert.ok(result.value.includes(preserved), result.value);
    assert.deepEqual(formatHxml(result.value), result);
  });
}

for (const source of [
  '<view><text>  x  </view>',
  '<view id="unterminated><text /></view>',
  '<view id=unquoted><text /></view>',
  '<view id="one" id="two" />',
  '<view><!-- unclosed</view>',
  '<view><![CDATA[unclosed</view>',
  '<view>{{ unclosed</view>',
  '<view>{% comment %}unclosed</view>',
  '<view>{% verbatim named %}unclosed{% endverbatim %}</view>',
  '<view>{% if ready %}<text />{% endfor %}</view>',
  '<view>{% if ready %}<text /></view>',
  '<view>{% if ready %}<text>{% endif %}x</text></view>',
  '<view>{% if ready %}<text>{% else %}x</text>{% endif %}</view>',
  '<view>{% if ready %}<text />{% else %}<text />{% else %}<text />{% endif %}</view>',
  '<view>{% else %}<text /></view>',
  '<view><{{ dynamic }} /></view>',
  '<view {% if ready %}id="x"{% endif %} />',
  '<!DOCTYPE view><view />',
  '<view>bad & raw</view>',
  '<view><!-- bad -- comment --></view>',
]) {
  test(`formatter rejects ambiguous or malformed input unchanged: ${source}`, () => {
    assert.deepEqual(formatHxml(source), {ok: false, value: source});
  });
}

test("formatter accepts balanced independent conditional branches", () => {
  const source = '<view>{% if ready %}<text>yes</text>{% else %}<text>no</text>{% endif %}</view>';
  const result = formatHxml(source);
  assert.equal(result.ok, true);
  assert.deepEqual(formatHxml(result.value), result);
});

test("formatter preserves all Django tokens without replacement markers", () => {
  const source = '<view>{% for item in items %}<text>{{ item }} {# note #}DJHVTOKEN0</text>{% empty %}<text>empty</text>{% endfor %}</view>';
  const result = formatHxml(source);
  assert.equal(result.ok, true);
  assert.match(result.value, /{{ item }} {# note #}DJHVTOKEN0/);
  assert.deepEqual(formatHxml(result.value), result);
});

for (const source of [
  '<view>{% %}<text /></view>',
  '<view>{{ }}</view>',
  '<view>{% unknown_block %}<text /></view>',
  '<view>{% if %}<text />{% endif %}</view>',
  '<view>{% if ready %}<text />{% else extra %}<text />{% endif %}</view>',
  '<view>{% if ready %}<text />{% endif extra %}</view>',
  '<view>{% block main %}<text />{% endblock other %}</view>',
  '<view><!-- ends with dash---><text /></view>',
]) {
  test(`formatter rejects unsupported or ambiguous syntax unchanged: ${source}`, () => {
    assert.deepEqual(formatHxml(source), {ok: false, value: source});
  });
}

for (const source of [
  '',
  ' \n ',
  '<view> \n </view>',
  '<view><text /><text /></view>',
  '<hv:view xmlns:hv="https://hyperview.org/hyperview"><hv:text>  x  </hv:text></hv:view>',
  '<view>{% with x=value %}{% if x %}<text>yes</text>{% endif %}{% endwith %}</view>',
  '<view>{% block main %}<text>x</text>{% endblock main %}</view>',
  '{% load dj_hyperview %}<view><text>x</text></view>',
  '<view>{% include "partial.xml" %}<text>x</text></view>',
]) {
  test(`formatter success remains idempotent: ${source}`, () => {
    const result = formatHxml(source);
    assert.equal(result.ok, true);
    assert.deepEqual(formatHxml(result.value), result);
  });
}

test("formatter retains exact quoted opening tags and meaningful text spans", () => {
  const opening = '<view\tid = "a > b" data-label = \'{{ value|default:"x" }}\' >';
  const text = '<text>  A &amp; B\n <text> inner </text>  </text>';
  const source = opening + text + '</view>';
  const expected = opening + '\n  ' + text + '\n</view>';

  assert.deepEqual(formatHxml(source), {ok: true, value: expected});
  assert.deepEqual(formatHxml(expected), {ok: true, value: expected});
});

test("formatter preserves inherited xml:space across custom and known descendants", () => {
  const source = '<view xmlns:app="urn:app" xml:space="preserve"> \n <app:label>  <view><text> x </text></view>  </app:label><view xml:space="default"> <text> y </text> </view> </view>';

  assert.deepEqual(formatHxml(source), {ok: true, value: source});
  assert.deepEqual(formatHxml(formatHxml(source).value), {ok: true, value: source});
});

for (const newline of ['\n', '\r\n']) {
  for (const token of ['{# not' + newline + 'a comment #}', '{{ label' + newline + '}}', '{% if ready' + newline + '%}<text />{% endif %}']) {
    test(`formatter rejects LF-spanning Django delimiters: ${JSON.stringify(token)}`, () => {
      // Django's token regex does not use DOTALL: LF makes these literal text.
      const source = '<view>' + token + '<text /></view>';
      assert.deepEqual(formatHxml(source), {ok: false, value: source});
    });
  }
}

for (const command of ['comment', 'verbatim', 'verbatim named']) {
  test(`formatter rejects LF-spanning raw Django closing delimiter: ${command}`, () => {
    const parts = command.split(' ');
    const source = '<view>{% ' + command + ' %}multiline\nbody{% end' + parts[0] + '\n' + (parts[1] || '') + ' %}</view>';
    assert.deepEqual(formatHxml(source), {ok: false, value: source});
  });
}

for (const source of [
  '<view>{# valid\rcomment #}<text /></view>',
  '<view><text>{{ label\r}}</text></view>',
  '<view>{% if ready\r%}<text />{% endif %}</view>',
  '<view>{% comment %}multiline\n{% endcomment\n%}\nbody{% endcomment %}<text /></view>',
  '<view>{% verbatim named %}multiline\n{% endverbatim\nnamed %}\nbody{% endverbatim named %}</view>',
  '<view>{% comment %}multiline\nbody{% endcomment\r%}</view>',
]) {
  test(`formatter preserves Django CR tokens and multiline raw bodies: ${JSON.stringify(source)}`, () => {
    const result = formatHxml(source);
    assert.equal(result.ok, true);
    if (source.includes('multiline')) {
      assert.equal(result.value, source);
    }
    assert.deepEqual(formatHxml(result.value), result);
  });
}
