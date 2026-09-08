(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.djHyperviewValidation = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  function createController(options) {
    let generation = 0;
    let pending = null;
    let started = false;

    function changed() {
      generation += 1;
      if (pending) {
        pending.abort();
      }
      if (started) {
        options.onState("stale");
      }
    }

    async function validate() {
      if (pending) {
        pending.abort();
      }
      const current = ++generation;
      const snapshot = options.read();
      const identity = JSON.stringify(snapshot);
      pending = new AbortController();
      started = true;
      options.onState("loading");
      try {
        const result = await options.send(snapshot, pending.signal);
        if (current !== generation) {
          return;
        }
        if (identity !== JSON.stringify(options.read())) {
          options.onState("stale");
          return;
        }
        options.onResult(result, snapshot);
        options.onState(result.ok ? "valid" : "error");
      } catch (_error) {
        if (current !== generation) {
          return;
        }
        options.onResult({
          ok: false,
          diagnostics: [{
            severity: "error",
            code: "transport_error",
            message: "Validation is unavailable. Check your connection and try again.",
            template: null,
            coordinate_space: null,
            line: null,
            column: null,
          }],
        }, snapshot);
        options.onState("error");
      } finally {
        if (current === generation) {
          pending = null;
        }
      }
    }

    return {changed: changed, validate: validate};
  }

  function readDraft(editor, name) {
    return {
      name: name ? name.value : "",
      content: editor.getValue(),
    };
  }

  function sourceLocation(diagnostic, name) {
    if (
      diagnostic.coordinate_space !== "source" ||
      diagnostic.template !== name ||
      !Number.isInteger(diagnostic.line) ||
      diagnostic.line < 1
    ) {
      return null;
    }
    return {
      line: diagnostic.line,
      column: Number.isInteger(diagnostic.column) && diagnostic.column > 0 ?
        diagnostic.column - 1 : 0,
    };
  }

  function install(textarea) {
    const widget = textarea.previousElementSibling;
    const editor = widget && widget.editor;
    const actions = textarea.closest(".django-ace-editor").nextElementSibling;
    const form = textarea.closest("form");
    if (!editor || !actions || !form || textarea.dataset.validationInstalled) {
      return;
    }
    const button = actions.querySelector(".djhv-validate-source");
    const status = actions.querySelector(".djhv-source-validation-status");
    const diagnostics = actions.nextElementSibling;
    const name = form.querySelector('[name="name"]');
    if (!button || !status || !diagnostics || !name) {
      return;
    }
    textarea.dataset.validationInstalled = "true";

    function showState(state) {
      const labels = {
        loading: "Validating…",
        valid: "Template source is valid.",
        error: "Template source is invalid.",
        stale: "Template changed; validate again.",
      };
      button.disabled = state === "loading";
      status.textContent = labels[state];
    }

    function showDiagnostics(items, snapshot) {
      diagnostics.replaceChildren();
      for (const item of items) {
        const row = document.createElement("p");
        row.className = "djhv-source-validation-diagnostic";
        const coordinate = item.line ? " · source line " + item.line : "";
        row.textContent = "Error: " + item.message + coordinate;
        const location = sourceLocation(item, snapshot.name);
        if (location) {
          const jump = document.createElement("button");
          jump.type = "button";
          jump.className = "button";
          jump.textContent = "Go to source";
          jump.addEventListener("click", function () {
            editor.gotoLine(location.line, location.column, true);
            editor.focus();
          });
          row.appendChild(jump);
        }
        diagnostics.appendChild(row);
      }
    }

    const controller = createController({
      read: function () {
        return readDraft(editor, name);
      },
      send: async function (payload, signal) {
        const csrf = form.querySelector('[name="csrfmiddlewaretoken"]');
        const response = await fetch(textarea.dataset.hyperviewValidationUrl, {
          method: "POST",
          credentials: "same-origin",
          signal: signal,
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrf ? csrf.value : "",
          },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (typeof result.ok !== "boolean" || !Array.isArray(result.diagnostics)) {
          throw new Error("Invalid validation response");
        }
        return result;
      },
      onState: showState,
      onResult: function (result, snapshot) {
        showDiagnostics(result.diagnostics, snapshot);
      },
    });

    button.addEventListener("click", controller.validate);
    editor.getSession().on("change", controller.changed);
    name.addEventListener("input", controller.changed);
  }

  if (typeof window === "object" && window.addEventListener) {
    window.addEventListener("load", function () {
      document.querySelectorAll(
        "textarea[data-hyperview-validation-url]"
      ).forEach(install);
    });
  }

  return {
    createController: createController,
    readDraft: readDraft,
    sourceLocation: sourceLocation,
  };
});
