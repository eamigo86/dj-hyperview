(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.djHyperviewPreviewRenderer = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const HV = "https://hyperview.org/hyperview";
  const CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; media-src 'none'; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'";
  const CONTAINERS = new Set(["screen", "body", "header", "view", "list", "section-list", "section", "section-title", "items", "item", "form"]);
  const INPUTS = new Set(["text-field", "text-area", "select-single", "select-multiple", "option", "switch", "date-field"]);
  const METADATA = new Set(["id", "style", "xmlns", "name", "value", "placeholder", "selected", "content-container-style"]);
  const ENUMS = {
    flexDirection: ["row", "column", "row-reverse", "column-reverse"],
    flexWrap: ["wrap", "nowrap", "wrap-reverse"],
    alignItems: ["flex-start", "flex-end", "center", "stretch", "baseline"],
    alignSelf: ["auto", "flex-start", "flex-end", "center", "stretch", "baseline"],
    justifyContent: ["flex-start", "flex-end", "center", "space-between", "space-around", "space-evenly"],
    textAlign: ["left", "right", "center", "justify"],
    fontStyle: ["normal", "italic"], fontWeight: ["normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900"],
    textDecorationLine: ["none", "underline", "line-through", "underline line-through"],
    borderStyle: ["solid", "dotted", "dashed"],
    fontFamily: ["serif", "sans-serif", "monospace"],
  };
  const LENGTHS = /^(?:width|height|minWidth|minHeight|maxWidth|maxHeight|margin(?:Top|Bottom|Left|Right|Start|End)?|padding(?:Top|Bottom|Left|Right|Start|End)?|border(?:Top|Bottom|Left|Right)?Width|border(?:TopLeft|TopRight|BottomLeft|BottomRight)?Radius|fontSize|lineHeight|letterSpacing|gap|rowGap|columnGap)$/;
  const COLOR = /^(?:#[\da-f]{3,8}|[a-z]{1,24}|(?:rgb|rgba|hsl|hsla)\([\d\s.,%+-]+\))$/i;
  const COLOR_ATTR = /^(?:color|backgroundColor|border(?:Top|Bottom|Left|Right)?Color)$/;
  const NUMBER = /^-?\d+(?:\.\d+)?$/;
  const camelToCss = key => key.replace(/[A-Z]/g, letter => "-" + letter.toLowerCase());

  function warning(warnings, message) {
    if (warnings.size < 50) warnings.add(message);
  }

  function styleDeclarations(element, warnings) {
    const declarations = {};
    for (const attr of Array.from(element.attributes || [])) {
      const key = attr.name, value = attr.value;
      if (key === "id" || key === "xmlns" || key.startsWith("xmlns:")) continue;
      let keys = [key];
      if (/^(margin|padding)(Horizontal|Vertical)$/.test(key)) {
        const axis = key.endsWith("Horizontal") ? ["Left", "Right"] : ["Top", "Bottom"];
        const base = key.startsWith("margin") ? "margin" : "padding";
        keys = axis.map(side => base + side);
      }
      for (const property of keys) {
        let safe = null;
        if (ENUMS[property] && ENUMS[property].includes(value)) safe = value;
        else if (COLOR_ATTR.test(property) && COLOR.test(value)) safe = value;
        else if (LENGTHS.test(property)) {
          if (NUMBER.test(value)) safe = value + "px";
          else if (/^-?\d+(?:\.\d+)?%$/.test(value)) safe = value;
          else if (value === "auto" && /^(?:margin|width|height)/.test(property)) safe = value;
        } else if (["flex", "flexGrow", "flexShrink", "opacity"].includes(property) && NUMBER.test(value)) safe = value;
        if (safe === null) warning(warnings, "Unsupported style: " + key + ".");
        else declarations[camelToCss(property).replace(/-start$/, "-inline-start").replace(/-end$/, "-inline-end")] = safe;
      }
    }
    return declarations;
  }

  function renderTree(root, targetDocument, screenIndex) {
    const warnings = new Set(), screens = [], styles = new Map();
    let count = 0;
    function walk(node, callback, depth) {
      count += 1;
      if (depth > 128 || count > 20000) throw new Error("Preview XML is too complex.");
      if (node.nodeType !== 1) return;
      if (callback(node) === false) return;
      for (const child of Array.from(node.childNodes || [])) walk(child, callback, depth + 1);
    }
    walk(root, node => {
      if (node.namespaceURI === HV && node.localName === "screen") screens.push(node);
    }, 0);
    const selected = screens[screenIndex || 0] || screens[0] || root;
    count = 0;
    walk(root, node => {
      if (node.namespaceURI === HV && node.localName === "screen" && node !== selected) return false;
      if (node.namespaceURI === HV && node.localName === "style") {
        styles.set(node.getAttribute("id"), styleDeclarations(node, warnings));
        if (Array.from(node.childNodes || []).some(child => child.nodeType === 1)) {
          warning(warnings, "Style modifiers are not simulated.");
        }
      }
    }, 0);
    function text(node, value) { node.appendChild(targetDocument.createTextNode(value)); }
    function placeholder(name) {
      const node = targetDocument.createElement("div");
      node.setAttribute("class", "placeholder");
      text(node, "[" + name + " — not simulated]");
      warning(warnings, "Component not simulated: " + name + ".");
      return node;
    }
    function render(node) {
      if (node.nodeType === 3 || node.nodeType === 4) return targetDocument.createTextNode(node.textContent);
      if (node.nodeType !== 1) return null;
      const name = node.localName;
      if (node.namespaceURI !== HV) return placeholder(name);
      if (name === "styles" || name === "style") return null;
      if (name === "behavior") {
        warning(warnings, "Native behavior actions are disabled."); return null;
      }
      if (!CONTAINERS.has(name) && !INPUTS.has(name) && name !== "text") return placeholder(name);
      const element = targetDocument.createElement(name === "text" ? "span" : "div");
      element.setAttribute("class", name === "text" ? "text" : INPUTS.has(name) ? "field" : "container");
      const declarations = {"border-style": "solid", "border-width": "0px"};
      for (const id of (node.getAttribute("style") || "").split(/\s+/).filter(Boolean)) {
        if (styles.has(id)) Object.assign(declarations, styles.get(id));
        else warning(warnings, "A referenced style is missing from this screen.");
      }
      for (const [property, value] of Object.entries(declarations)) {
        // Set only generated allowlisted declarations, never source CSS text.
        if (element.style.setProperty) element.style.setProperty(property, value);
        else element.style[property] = value;
      }
      for (const attr of Array.from(node.attributes || [])) {
        if (!METADATA.has(attr.name) && !attr.name.startsWith("xmlns:")) {
          warning(warnings, "Attribute not simulated: " + name + "." + attr.name + ".");
        }
        if (attr.name === "content-container-style") warning(warnings, "Content container styles are not simulated.");
      }
      if (INPUTS.has(name)) text(element, node.getAttribute("value") || node.getAttribute("placeholder") || "[" + name + "]");
      for (const child of Array.from(node.childNodes || [])) {
        const rendered = render(child); if (rendered) element.appendChild(rendered);
      }
      return element;
    }
    return {node: render(selected), warnings: Array.from(warnings), screens: screens.map((screen, index) => screen.getAttribute("id") || "Screen " + (index + 1))};
  }

  function renderHxml(hxml, ownerDocument, screenIndex) {
    if (typeof hxml !== "string" || /<!DOCTYPE|<!ENTITY/i.test(hxml)) throw new Error("Unsafe XML declaration.");
    const parser = new ownerDocument.defaultView.DOMParser();
    const xml = parser.parseFromString(hxml, "application/xml");
    if (xml.getElementsByTagName("parsererror").length || !xml.documentElement) throw new Error("Invalid XML.");
    const output = ownerDocument.implementation.createHTMLDocument("Static Hyperview preview");
    const meta = output.createElement("meta");
    meta.setAttribute("http-equiv", "Content-Security-Policy"); meta.setAttribute("content", CSP);
    output.head.insertBefore(meta, output.head.firstChild);
    const baseStyle = output.createElement("style");
    baseStyle.textContent = "body{margin:0;padding:16px;background:#fff;color:#222;font-family:system-ui,sans-serif;font-size:16px;overflow-wrap:anywhere}.container{display:flex;flex-direction:column;min-width:0}.text{white-space:pre-wrap}.field,.placeholder{padding:10px;margin:4px 0;border:1px dashed #888;border-radius:4px}.placeholder{color:#555;background:#f4f4f4}";
    output.head.appendChild(baseStyle);
    const result = renderTree(xml.documentElement, output, screenIndex);
    if (result.node) output.body.appendChild(result.node);
    return {srcdoc: "<!doctype html>" + output.documentElement.outerHTML, warnings: result.warnings, screens: result.screens};
  }
  return {styleDeclarations, renderTree, renderHxml};
});
