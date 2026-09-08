(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.djHyperviewPreview = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  function createController(options) {
    let generation = 0, pending = null, started = false;
    function changed() {
      generation += 1;
      if (pending) pending.abort();
      if (started) options.onState("stale");
    }
    async function refresh() {
      if (pending) pending.abort();
      const current = ++generation;
      const snapshot = options.read();
      const identity = JSON.stringify(snapshot);
      pending = new AbortController(); started = true;
      options.onState("loading");
      try {
        const result = await options.send(snapshot, pending.signal);
        if (current !== generation) return;
        if (identity !== JSON.stringify(options.read())) {
          options.onState("stale"); return;
        }
        options.onResult(result, snapshot);
        options.onState(!result.ok ? "error" : result.diagnostics.some(item => item.severity === "warning") ? "warning" : "valid");
      } catch (_error) {
        if (current !== generation) return;
        options.onResult({ok: false, hxml: null, diagnostics: [{severity: "error", code: "transport_error", message: "Preview is unavailable. Check your connection and try again.", template: null, coordinate_space: null, line: null, column: null}]}, snapshot);
        options.onState("error");
      }
    }
    return {changed, refresh};
  }

  function sourceLocation(diagnostic, name) {
    if (diagnostic.coordinate_space !== "source" || diagnostic.template !== name || !Number.isInteger(diagnostic.line) || diagnostic.line < 1) return null;
    return {line: diagnostic.line, column: Number.isInteger(diagnostic.column) && diagnostic.column > 0 ? diagnostic.column - 1 : 0};
  }

  function renderedLocation(diagnostic, hxml) {
    if (diagnostic.coordinate_space !== "rendered" || !Number.isInteger(diagnostic.line) || diagnostic.line < 1) return null;
    const lines = hxml.split("\n");
    if (diagnostic.line > lines.length) return null;
    const start = lines.slice(0, diagnostic.line - 1).reduce((total, line) => total + line.length + 1, 0);
    return {line: diagnostic.line, start, end: start + lines[diagnostic.line - 1].length};
  }

  function readDraft(textarea, editor, name, scenario) {
    return {name: name ? name.value : textarea.dataset.hyperviewTemplateName || "", content: editor.getValue(), scenario: scenario.value};
  }

  function install(textarea) {
    const editor = textarea.previousElementSibling && textarea.previousElementSibling.editor;
    const workspace = textarea.closest(".djhv-preview-workspace");
    if (!editor || !workspace || workspace.dataset.previewInstalled) return;
    workspace.dataset.previewInstalled = "true";
    const form = textarea.closest("form");
    const name = form.querySelector('[name="name"]');
    const scenario = workspace.querySelector(".djhv-preview-scenario");
    const button = workspace.querySelector(".djhv-preview-hxml");
    const state = workspace.querySelector(".djhv-preview-state");
    const diagnostics = workspace.querySelector(".djhv-preview-diagnostics");
    const output = workspace.querySelector(".djhv-preview-output");
    const frameHost = workspace.querySelector(".djhv-preview-frame");
    const screen = workspace.querySelector(".djhv-preview-screen");
    let currentResult = null, currentName = "", renderWarnings = [], currentState = "pending";
    const labels = {loading: "Rendering…", valid: "Valid · static preview", warning: "Preview with warnings", error: "Preview failed", stale: "Out of date · preview again"};
    function showState(value) {
      currentState = value;
      button.disabled = value === "loading";
      const display = value === "valid" && renderWarnings.length ? "warning" : value;
      state.textContent = labels[display]; workspace.dataset.previewState = display;
      workspace.setAttribute("aria-busy", value === "loading" ? "true" : "false");
    }
    function showDiagnostics(items) {
      diagnostics.replaceChildren();
      for (const item of items) {
        const row = document.createElement("p");
        row.className = "djhv-preview-diagnostic " + (item.severity === "error" ? "djhv-preview-error" : "djhv-preview-warning");
        const coordinate = item.line ? " · " + (item.coordinate_space === "rendered" ? "rendered HXML" : "source") + " line " + item.line : "";
        row.textContent = (item.severity === "error" ? "Error: " : "Warning: ") + item.message + (item.template ? " · " + item.template : "") + coordinate;
        const location = sourceLocation(item, currentName);
        if (location) {
          const jump = document.createElement("button"); jump.type = "button"; jump.textContent = "Go to source";
          jump.addEventListener("click", function () {
            if (currentState === "stale" || currentState === "loading") return;
            editor.gotoLine(location.line, location.column, true); editor.focus();
          });
          row.appendChild(jump);
        }
        const rendered = renderedLocation(item, output.textContent);
        if (rendered) {
          const jump = document.createElement("button"); jump.type = "button"; jump.textContent = "Go to rendered HXML";
          jump.addEventListener("click", function () {
            output.closest("details").open = true;
            output.focus();
            output.scrollTop = (rendered.line - 1) * (parseFloat(window.getComputedStyle(output).lineHeight) || 20);
            if (output.firstChild) {
              const range = document.createRange();
              range.setStart(output.firstChild, rendered.start); range.setEnd(output.firstChild, rendered.end);
              const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
            }
          });
          row.appendChild(jump);
        }
        diagnostics.appendChild(row);
      }
    }
    function draw(index) {
      frameHost.replaceChildren(); renderWarnings = [];
      screen.replaceChildren(); screen.closest("label").hidden = true;
      if (!currentResult || !currentResult.ok || !currentResult.hxml) return;
      try {
        const view = window.djHyperviewPreviewRenderer.renderHxml(currentResult.hxml, document, index);
        const iframe = document.createElement("iframe");
        iframe.title = "Static Hyperview preview";
        iframe.setAttribute("sandbox", ""); iframe.setAttribute("referrerpolicy", "no-referrer");
        iframe.srcdoc = view.srcdoc;
        frameHost.appendChild(iframe);
        screen.replaceChildren();
        view.screens.forEach(function (label, position) {
          const option = document.createElement("option"); option.value = String(position); option.textContent = label; screen.appendChild(option);
        });
        screen.value = String(index);
        screen.closest("label").hidden = view.screens.length < 2;
        renderWarnings = view.warnings.map(message => ({severity: "warning", code: "not_simulated", message}));
      } catch (_error) {
        renderWarnings = [{severity: "warning", code: "visual_unavailable", message: "Static visualization is unavailable; inspect the rendered HXML."}];
      }
      showDiagnostics(currentResult.diagnostics.concat(renderWarnings));
    }
    const controller = createController({
      read: () => readDraft(textarea, editor, name, scenario),
      send: async function (payload, signal) {
        const csrf = form.querySelector('[name="csrfmiddlewaretoken"]');
        const response = await fetch(textarea.dataset.hyperviewPreviewUrl, {
          method: "POST", credentials: "same-origin", signal,
          headers: {"Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.value : ""},
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (typeof result.ok !== "boolean" || !Array.isArray(result.diagnostics)) throw new Error("Invalid preview response");
        return result;
      },
      onState: showState,
      onResult: function (result, snapshot) {
        currentResult = result; currentName = snapshot.name;
        output.textContent = result.hxml || "";
        showDiagnostics(result.diagnostics); draw(0);
      },
    });
    button.addEventListener("click", controller.refresh);
    editor.getSession().on("change", controller.changed);
    if (name) name.addEventListener("input", controller.changed);
    scenario.addEventListener("change", controller.changed);
    screen.addEventListener("change", function () {
      draw(Number(screen.value));
      showState(currentState);
    });
  }
  if (typeof window === "object" && window.addEventListener) {
    window.addEventListener("load", function () {
      document.querySelectorAll("textarea[data-hyperview-preview-url]").forEach(install);
    });
  }
  return {createController, sourceLocation, renderedLocation, readDraft};
});
