ace.define(
  "ace/mode/hxml_highlight_rules",
  ["require", "exports", "module", "ace/mode/xml_highlight_rules", "ace/lib/oop"],
  function (require, exports) {
    "use strict";
    const XmlHighlightRules = require("ace/mode/xml_highlight_rules").XmlHighlightRules;
    const oop = require("ace/lib/oop");

    const HxmlHighlightRules = function () {
      XmlHighlightRules.call(this);
      this.$rules.start.unshift({
        token: "keyword.control.django",
        regex: "\\{\\{.*?\\}\\}|\\{%.*?%\\}|\\{#.*?#\\}",
      });
      this.normalizeRules();
    };
    oop.inherits(HxmlHighlightRules, XmlHighlightRules);
    exports.HxmlHighlightRules = HxmlHighlightRules;
  },
);

ace.define(
  "ace/mode/hxml",
  ["require", "exports", "module", "ace/mode/xml", "ace/mode/hxml_highlight_rules", "ace/lib/oop"],
  function (require, exports) {
    "use strict";
    const XmlMode = require("ace/mode/xml").Mode;
    const HxmlHighlightRules = require("ace/mode/hxml_highlight_rules").HxmlHighlightRules;
    const oop = require("ace/lib/oop");

    const Mode = function () {
      XmlMode.call(this);
      this.HighlightRules = HxmlHighlightRules;
    };
    oop.inherits(Mode, XmlMode);
    Mode.prototype.$id = "ace/mode/hxml";
    exports.Mode = Mode;
  },
);
