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

  function formatProtected(source, prefix) {
    if (/[{](?:[{%#])/.test(source)) {
      throw new Error("unprotected Django syntax");
    }
    const tokenizer = new RegExp(
      "<!--[\\s\\S]*?-->|<[^>]*>|" + escapePattern(prefix) + "\\d+|[^<]+",
      "g",
    );
    const placeholder = new RegExp("^" + escapePattern(prefix) + "\\d+$");
    const parts = source.match(tokenizer) || [];
    if (parts.join("") !== source) {
      throw new Error("unrecognized input");
    }
    const lines = [];
    const stack = [];
    let depth = 0;
    for (let index = 0; index < parts.length; index += 1) {
      const token = parts[index];
      if (!token.trim()) {
        continue;
      }
      if (/^<\//.test(token)) {
        const name = tagName(token);
        if (!name || stack.pop() !== name) {
          throw new Error("unbalanced XML");
        }
        depth -= 1;
        lines.push("  ".repeat(depth) + token.trim());
        continue;
      }
      if (/^</.test(token)) {
        if (/^<\?|^<!/.test(token) || /\/\s*>$/.test(token)) {
          lines.push("  ".repeat(depth) + token.trim());
          continue;
        }
        const name = tagName(token);
        if (!name) {
          throw new Error("invalid tag");
        }
        const text = parts[index + 1];
        const closing = parts[index + 2];
        const escaped = escapePattern(name);
        if (
          text &&
          closing &&
          !/^</.test(text) &&
          new RegExp("^</\\s*" + escaped + "\\s*>$").test(closing)
        ) {
          lines.push("  ".repeat(depth) + token.trim() + text.trim() + closing.trim());
          index += 2;
          continue;
        }
        lines.push("  ".repeat(depth) + token.trim());
        stack.push(name);
        depth += 1;
        continue;
      }
      if (placeholder.test(token.trim())) {
        lines.push("  ".repeat(depth) + token.trim());
        continue;
      }
      throw new Error("mixed text cannot be formatted safely");
    }
    if (stack.length) {
      throw new Error("unbalanced XML");
    }
    return lines.join("\n");
  }

  function formatHxml(source) {
    try {
      const protectedValue = protectDjango(source);
      const formatted = formatProtected(protectedValue.source, protectedValue.prefix);
      return {
        ok: true,
        value: restoreDjango(
          formatted,
          protectedValue.tokens,
          protectedValue.prefix,
        ),
      };
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
    const button = actions.querySelector(".djhv-format-hxml");
    const status = actions.querySelector(".djhv-editor-status");
    button.addEventListener("click", function () {
      const result = formatHxml(editor.getValue());
      if (result.ok) {
        editor.setValue(result.value, -1);
        status.textContent = "HXML formatted.";
      } else {
        status.textContent = "HXML could not be formatted safely; no changes were made.";
      }
    });

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
