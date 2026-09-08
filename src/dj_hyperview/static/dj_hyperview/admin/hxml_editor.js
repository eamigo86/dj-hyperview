(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.djHyperviewEditor = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const HV_NAMESPACE = "https://hyperview.org/hyperview";
  const DJANGO_TOKEN = /({{[\s\S]*?}}|{%[\s\S]*?%}|{#[\s\S]*?#})/g;

  function escapePattern(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function protectDjango(source) {
    const tokens = [];
    let prefix = "DJHVTOKEN";
    while (source.includes(prefix)) {
      prefix += "_";
    }
    const protectedSource = source.replace(DJANGO_TOKEN, function (token) {
      const marker = prefix + tokens.length;
      tokens.push(token);
      return marker;
    });
    return {source: protectedSource, tokens: tokens, prefix: prefix};
  }

  function restoreDjango(source, tokens, prefix) {
    const marker = new RegExp(escapePattern(prefix || "DJHVTOKEN") + "(\\d+)", "g");
    return source.replace(marker, function (_marker, index) {
      return tokens[Number(index)];
    });
  }

  function tagName(token) {
    const match = token.match(/^<\/?\s*([^\s/>]+)/);
    return match ? match[1] : null;
  }

  // Only known element-only Hyperview containers may have whitespace rewritten.
  // Text-bearing and custom subtrees retain their original source spans.
  const STRUCTURAL_ELEMENTS = new Set([
    "screen", "styles", "body", "header", "view", "list", "section-list",
    "section", "section-title", "items", "item", "form",
  ]);
  const DJANGO_BLOCKS = new Set(["if", "for", "with", "block", "autoescape"]);
  const DJANGO_SINGLE_TAGS = new Set([
    "load", "extends", "include", "url", "now", "cycle", "resetcycle", "firstof",
    "lorem", "csrf_token", "debug", "templatetag", "widthratio", "regroup",
    "translate", "trans", "static", "get_static_prefix", "get_media_prefix",
  ]);
  const XML_NAME = /[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?/y;

  function djangoToken(source, start) {
    const opener = source.slice(start, start + 2);
    const closer = {"{{": "}}", "{%": "%}", "{#": "#}"}[opener];
    if (!closer) {
      return null;
    }
    const boundary = source.indexOf(closer, start + 2);
    if (boundary < 0) {
      throw new Error("incomplete Django token");
    }
    let end = boundary + 2;
    const tokenBody = source.slice(start + 2, boundary);
    // Django's lexer matches CR, but never LF, inside a template delimiter.
    if (tokenBody.includes("\n")) {
      throw new Error("LF-spanning Django delimiter");
    }
    const body = tokenBody.trim();
    if (!body && opener !== "{#") {
      throw new Error("empty Django token");
    }
    const command = opener === "{%" ? body.split(/\s+/, 1)[0] : null;
    const argument = command ? body.slice(command.length).trim() : "";
    let kind = opener === "{{" ? "variable" : "django";
    if (["comment", "verbatim", "blocktranslate", "blocktrans"].includes(command)) {
      const named = command === "verbatim" ? body.match(/^verbatim(?:\s+(\S+))?$/) : null;
      if (command === "verbatim" && !named) {
        throw new Error("ambiguous verbatim block");
      }
      const spacing = "[^\\S\\n]";
      const suffix = named && named[1] ? spacing + "+" + escapePattern(named[1]) : "";
      const closingCommand = ["blocktranslate", "blocktrans"].includes(command) ?
        "(?:endblocktranslate|endblocktrans)" : "end" + command;
      const closing = new RegExp("{%" + spacing + "*" + closingCommand + suffix + spacing + "*%}", "g");
      closing.lastIndex = end;
      const match = closing.exec(source);
      if (!match) {
        throw new Error("incomplete raw Django block");
      }
      end = closing.lastIndex;
      kind = "raw";
    }
    return {kind: kind, command: command, argument: argument, start: start, end: end};
  }

  function assertXmlText(value) {
    if (/&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[\da-fA-F]+;)/.test(value) ||
        /[\u0000-\u0008\u000b\u000c\u000e-\u001f]|\]\]>/.test(value)) {
      throw new Error("invalid XML text");
    }
  }

  function xmlTag(source, start) {
    let cursor = start + 1;
    const closing = source[cursor] === "/";
    if (closing) {
      cursor += 1;
    }
    function readName() {
      XML_NAME.lastIndex = cursor;
      const match = XML_NAME.exec(source);
      if (!match) {
        throw new Error("invalid XML name");
      }
      cursor = XML_NAME.lastIndex;
      return match[0];
    }
    function skipSpace() {
      const previous = cursor;
      while (/[\t\n\r ]/.test(source[cursor] || "")) {
        cursor += 1;
      }
      return cursor > previous;
    }
    const name = readName();
    const attributes = Object.create(null);
    while (cursor < source.length) {
      const separated = skipSpace();
      if (source[cursor] === ">" || (!closing && source.startsWith("/>", cursor))) {
        const selfClosing = source[cursor] === "/";
        return {
          kind: closing ? "close" : "element", name: name, attributes: attributes,
          start: start, end: cursor + (selfClosing ? 2 : 1), selfClosing: selfClosing,
        };
      }
      if (closing || !separated) {
        throw new Error("invalid XML tag");
      }
      const attribute = readName();
      if (Object.hasOwn(attributes, attribute)) {
        throw new Error("duplicate XML attribute");
      }
      skipSpace();
      if (source[cursor++] !== "=") {
        throw new Error("missing attribute value");
      }
      skipSpace();
      const quote = source[cursor++];
      if (quote !== '"' && quote !== "'") {
        throw new Error("unquoted XML attribute");
      }
      const valueStart = cursor;
      let segment = cursor;
      while (cursor < source.length && source[cursor] !== quote) {
        const token = djangoToken(source, cursor);
        if (token) {
          assertXmlText(source.slice(segment, cursor));
          if (token.kind === "raw" || token.command) {
            throw new Error("branch-dependent XML attribute");
          }
          cursor = token.end;
          segment = cursor;
        } else if (source[cursor] === "<") {
          throw new Error("unescaped XML attribute");
        } else {
          cursor += 1;
        }
      }
      if (cursor >= source.length) {
        throw new Error("incomplete XML attribute");
      }
      assertXmlText(source.slice(segment, cursor));
      attributes[attribute] = source.slice(valueStart, cursor);
      cursor += 1;
    }
    throw new Error("incomplete XML tag");
  }

  function formatSpans(source) {
    const root = {children: [], namespaces: {"": HV_NAMESPACE}, preserve: false};
    const stack = [root];
    const blocks = [];
    let cursor = 0;
    function sameStack(snapshot) {
      return snapshot.length === stack.length &&
        snapshot.every(function (node, index) { return node === stack[index]; });
    }
    while (cursor < source.length) {
      const parent = stack[stack.length - 1];
      let token = djangoToken(source, cursor);
      if (token) {
        if (token.kind === "raw" || token.kind === "variable") {
          parent.preserve = true;
        } else if (token.command) {
          const command = token.command;
          if (DJANGO_BLOCKS.has(command)) {
            if (!token.argument) {
              throw new Error("missing Django block arguments");
            }
            blocks.push({
              command: command, argument: token.argument,
              stack: stack.slice(), terminal: false,
            });
          } else if (["else", "elif", "empty"].includes(command) || command.startsWith("end")) {
            const block = blocks[blocks.length - 1];
            if (!block || !sameStack(block.stack)) {
              throw new Error("branch-dependent XML structure");
            }
            if (command === "end" + block.command) {
              if (token.argument && (block.command !== "block" || token.argument !== block.argument)) {
                throw new Error("mismatched Django block arguments");
              }
              blocks.pop();
            } else if (!block.terminal && (
              (block.command === "if" && ["else", "elif"].includes(command)) ||
              (block.command === "for" && command === "empty")
            )) {
              if ((command === "elif") !== Boolean(token.argument)) {
                throw new Error("invalid Django branch arguments");
              }
              block.terminal = command !== "elif";
            } else {
              throw new Error("unbalanced Django branch");
            }
          } else if (DJANGO_SINGLE_TAGS.has(command)) {
            // Output-producing tags have unknown whitespace semantics.
            parent.preserve = true;
          } else {
            throw new Error("unsupported Django tag");
          }
        }
      } else if (source.startsWith("<!--", cursor) || source.startsWith("<![CDATA[", cursor) || source.startsWith("<?", cursor)) {
        const comment = source.startsWith("<!--", cursor);
        const cdata = source.startsWith("<![CDATA[", cursor);
        const openerLength = comment ? 4 : cdata ? 9 : 2;
        const closer = comment ? "-->" : cdata ? "]]>" : "?>";
        const end = source.indexOf(closer, cursor + openerLength);
        if (end < 0 || (comment && /--|-$/.test(source.slice(cursor + 4, end)))) {
          throw new Error("incomplete or invalid XML special section");
        }
        token = {start: cursor, end: end + closer.length, kind: "literal"};
        if (cdata) {
          parent.preserve = true;
        }
      } else if (source[cursor] === "<") {
        token = xmlTag(source, cursor);
        if (token.kind === "close") {
          if (stack.length === 1 || parent.name !== token.name) {
            throw new Error("unbalanced XML");
          }
          parent.closeStart = cursor;
          parent.end = token.end;
          stack.pop();
          cursor = token.end;
          continue;
        }
        token.openEnd = token.end;
        token.children = [];
        token.namespaces = {...parent.namespaces};
        for (const [attribute, value] of Object.entries(token.attributes)) {
          if (attribute === "xmlns" || attribute.startsWith("xmlns:")) {
            token.namespaces[attribute === "xmlns" ? "" : attribute.slice(6)] = value;
          }
        }
        const splitName = token.name.split(":");
        const prefix = splitName.length === 2 ? splitName[0] : "";
        const localName = splitName[splitName.length - 1];
        token.preserve = token.namespaces[prefix] !== HV_NAMESPACE ||
          !STRUCTURAL_ELEMENTS.has(localName) ||
          (Object.hasOwn(token.attributes, "xml:space") && token.attributes["xml:space"] !== "default") ||
          Object.hasOwn(token.attributes, "preformatted");
        if (!token.selfClosing) {
          stack.push(token);
        }
      } else {
        const boundary = /<|{[{%#]/g;
        boundary.lastIndex = cursor;
        const next = boundary.exec(source);
        const end = next ? next.index : source.length;
        const text = source.slice(cursor, end);
        assertXmlText(text);
        token = {start: cursor, end: end, kind: "text"};
        if (/[^\t\n\r ]/.test(text)) {
          parent.preserve = true;
        }
      }
      parent.children.push(token);
      cursor = token.end;
    }
    if (stack.length !== 1 || blocks.length) {
      throw new Error("unbalanced template");
    }
    function render(node, depth) {
      if (node.preserve || !node.children || node.selfClosing || !node.children.length) {
        return source.slice(node.start, node.end);
      }
      const meaningful = node.children.filter(function (child) {
        return child.kind !== "text";
      });
      if (!meaningful.length) {
        return source.slice(node.start, node.end);
      }
      const children = meaningful.map(function (child) {
        return "  ".repeat(depth) + render(child, depth + 1);
      }).join("\n");
      if (node === root) {
        return children;
      }
      return source.slice(node.start, node.openEnd) + "\n" + children + "\n" +
        "  ".repeat(depth - 1) + source.slice(node.closeStart, node.end);
    }
    if (root.preserve) {
      return source;
    }
    return render(root, 0);
  }

  function formatHxml(source) {
    try {
      return {ok: true, value: formatSpans(source)};
    } catch (_error) {
      return {ok: false, value: source};
    }
  }

  function namespaces(source) {
    const result = {};
    const pattern = /xmlns(?::([\w-]+))?\s*=\s*["']([^"']+)["']/g;
    for (const match of source.matchAll(pattern)) {
      result[match[1] || ""] = match[2];
    }
    return result;
  }

  function catalogKey(name, declared) {
    const separator = name.indexOf(":");
    const prefix = separator >= 0 ? name.slice(0, separator) : "";
    const local = separator >= 0 ? name.slice(separator + 1) : name;
    const namespace = declared[prefix] || (prefix ? "" : HV_NAMESPACE);
    return namespace === HV_NAMESPACE ? local : "{" + namespace + "}" + local;
  }

  function displayName(key, declared) {
    if (!key.startsWith("{")) {
      return key;
    }
    const boundary = key.indexOf("}");
    const namespace = key.slice(1, boundary);
    const local = key.slice(boundary + 1);
    const prefix = Object.keys(declared).find(function (candidate) {
      return candidate && declared[candidate] === namespace;
    });
    return prefix ? prefix + ":" + local : null;
  }

  function parentElement(source) {
    const stack = [];
    const pattern = /<\/?\s*[^!?][^>]*>/g;
    for (const match of source.matchAll(pattern)) {
      const token = match[0];
      const name = tagName(token);
      if (!name) {
        continue;
      }
      if (/^<\//.test(token)) {
        stack.pop();
      } else if (!/\/\s*>$/.test(token)) {
        stack.push(name);
      }
    }
    return stack.length ? stack[stack.length - 1] : null;
  }

  function getCompletions(catalog, source, cursor) {
    const before = source.slice(0, cursor);
    const left = before.lastIndexOf("<");
    const right = before.lastIndexOf(">");
    if (left <= right || /^<\//.test(before.slice(left))) {
      return [];
    }
    const fragment = before.slice(left + 1);
    const declared = namespaces(before);
    const tagMatch = fragment.match(/^([^\s/>]+)/);
    if (!tagMatch || !/\s/.test(fragment)) {
      const typed = tagMatch ? tagMatch[1] : "";
      const parent = parentElement(before.slice(0, left));
      let keys = Object.keys(catalog.elements);
      if (parent) {
        const definition = catalog.elements[catalogKey(parent, declared)];
        if (!definition) {
          return [];
        }
        keys = definition.children.slice();
        if (definition.allows_custom_children) {
          keys = keys.concat(Object.keys(catalog.elements).filter(function (key) {
            return catalog.elements[key].namespace !== HV_NAMESPACE;
          }));
        }
      }
      return Array.from(new Set(keys.map(function (key) {
        return displayName(key, declared);
      }).filter(function (name) {
        return name && name.startsWith(typed);
      }))).sort();
    }

    const definition = catalog.elements[catalogKey(tagMatch[1], declared)];
    if (!definition) {
      return [];
    }
    const valueMatch = fragment.match(/([\w:-]+)\s*=\s*["']([^"']*)$/);
    if (valueMatch) {
      const attribute = definition.attributes[valueMatch[1]];
      const typed = valueMatch[2];
      return attribute ? attribute.enum.filter(function (value) {
        return value.startsWith(typed);
      }).sort() : [];
    }
    const used = new Set();
    for (const match of fragment.matchAll(/\s([\w:-]+)\s*=/g)) {
      used.add(match[1]);
    }
    const partialMatch = fragment.match(/\s([\w:-]*)$/);
    const partial = partialMatch ? partialMatch[1] : "";
    return Object.keys(definition.attributes).filter(function (name) {
      return !used.has(name) && name.startsWith(partial);
    }).sort();
  }

  function syncEditor(textarea, editor) {
    textarea.value = editor.getValue();
  }

  function installEditor(textarea) {
    const widget = textarea.previousElementSibling;
    const editor = widget && widget.editor;
    if (!editor) {
      return;
    }
    const HxmlMode = window.ace.require("ace/mode/hxml").Mode;
    editor.getSession().setMode(new HxmlMode());
    const form = textarea.closest("form");
    if (form) {
      form.addEventListener("submit", function () {
        syncEditor(textarea, editor);
      });
    }
    const actions = textarea.closest(".django-ace-editor").nextElementSibling;
    const status = actions.querySelector(".djhv-editor-status");

    const setTheme = function () {
      const selected = document.documentElement.dataset.theme;
      const dark = selected === "dark" ||
        (!selected && window.matchMedia("(prefers-color-scheme: dark)").matches);
      editor.setTheme(dark ? "ace/theme/monokai" : "ace/theme/textmate");
    };
    setTheme();
    new MutationObserver(setTheme).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    const catalogUrl = textarea.dataset.hyperviewCatalogUrl;
    if (!catalogUrl) {
      return;
    }
    fetch(catalogUrl, {credentials: "same-origin"}).then(function (response) {
      if (!response.ok) {
        throw new Error("catalog unavailable");
      }
      return response.json();
    }).then(function (catalog) {
      const languageTools = window.ace.require("ace/ext/language_tools");
      languageTools.addCompleter({
        getCompletions: function (_editor, session, position, _prefix, callback) {
          const source = session.getValue();
          const cursor = session.doc.positionToIndex(position);
          const names = getCompletions(catalog, source, cursor);
          callback(null, names.map(function (name) {
            return {caption: name, value: name, meta: "HXML", score: 1000};
          }));
        },
      });
    }).catch(function () {
      status.textContent = "HXML completions are temporarily unavailable.";
    });
  }

  function install() {
    document.querySelectorAll("textarea[data-hyperview-editor='true']").forEach(installEditor);
  }

  if (typeof window === "object" && window.addEventListener) {
    window.addEventListener("load", install);
  }

  return {
    formatHxml: formatHxml,
    getCompletions: getCompletions,
    protectDjango: protectDjango,
    restoreDjango: restoreDjango,
    syncEditor: syncEditor,
  };
});
