/* Qizil UI — renders an optimization report. No frameworks, no network beyond
   this app's own API. Works in two modes:
     - interactive: talks to the local `qizil ui` server
     - static:      renders window.QIZIL_DATA embedded by `qizil report`      */

(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var STATIC = window.QIZIL_DATA || null;
  var EXAMPLES = [];
  var state = { level: 3, gateset: "auto", phase: false, verify: true, source: null, name: "" };

  // ---------------------------------------------------------------- helpers

  function el(tag, attrs) {
    var node = document.createElement(tag);
    apply(node, attrs);
    add(node, [].slice.call(arguments, 2));
    return node;
  }

  function s(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    apply(node, attrs, true);
    add(node, [].slice.call(arguments, 2));
    return node;
  }

  function apply(node, attrs, isSvg) {
    if (!attrs) return;
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.setAttribute("class", value);
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (!isSvg && key in node && key !== "style") node[key] = value;
      else node.setAttribute(key, value);
    });
  }

  function add(node, kids) {
    kids.forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) return add(node, kid);
      node.appendChild(kid.nodeType ? kid : document.createTextNode(String(kid)));
    });
  }

  function num(value) {
    if (value === null || value === undefined) return "-";
    if (typeof value !== "number") return String(value);
    if (Number.isInteger(value)) return value.toLocaleString("en-US");
    var size = Math.abs(value);
    if (size >= 1000) return Math.round(value).toLocaleString("en-US");
    if (size >= 1) return value.toFixed(2);
    if (size === 0) return "0";
    if (size < 0.01) return value.toExponential(2);
    return value.toFixed(3);
  }

  function delta(before, after) {
    if (before === after) return { text: "same", cls: "flat" };
    if (!before) return { text: after > 0 ? "new" : "-", cls: after > before ? "bad" : "good" };
    var change = ((after - before) / before) * 100;
    return {
      text: (change > 0 ? "+" : "") + change.toFixed(1) + "%",
      cls: change < 0 ? "good" : "bad"
    };
  }

  function panel(title, hint, extraClass) {
    var node = el("section", { class: "panel" + (extraClass ? " " + extraClass : "") },
      el("h2", { text: title }));
    if (hint) node.appendChild(el("p", { class: "hint", text: hint }));
    return node;
  }

  // ------------------------------------------------------------ proof panel

  function renderProof(data) {
    var v = data.verification;
    var facts = [];
    var cls, glyph, headline;

    if (!v) {
      cls = "warn"; glyph = "!"; headline = "Equivalence check not run";
    } else if (v.equivalent) {
      cls = "ok"; glyph = "✓";
      headline = "Unitary preserved — U_out ≡ U_in";
      facts.push(["max error", v.max_error.toExponential(2)]);
      facts.push(["segments", v.segments_checked]);
      facts.push(["simulator", v.backend]);
    } else if (!v.available) {
      cls = "warn"; glyph = "!"; headline = "Equivalence check skipped";
      facts.push(["reason", (v.messages || []).join("; ") || "unavailable"]);
    } else {
      cls = "fail"; glyph = "✗"; headline = "NOT equivalent — rewrite rejected";
      facts.push(["detail", (v.messages || []).join("; ")]);
    }

    if (data.global_phase) {
      facts.push(["global phase", data.global_phase.toFixed(6) + " rad"]);
    }
    if (data.llvm && data.llvm.checked) {
      facts.push(["LLVM verifier", data.llvm.diagnostic ? "failed" : "passed"]);
    }

    return el("div", { class: "proof " + cls },
      el("span", { class: "glyph", text: glyph }),
      el("span", { class: "headline", text: headline }),
      el("div", { class: "facts" }, facts.map(function (f) {
        return el("span", { class: "fact" }, f[0] + " ", el("b", { text: String(f[1]) }));
      }))
    );
  }

  // ---------------------------------------------------------- metric panels

  function bars(before, after) {
    var scale = Math.max(before, after, 1);
    function row(kind, value) {
      return el("div", { class: "bar " + kind },
        el("span", { class: "track" },
          el("span", { class: "fill", style: "width:" + (value / scale) * 100 + "%" })),
        el("span", { class: "value", text: num(value) }));
    }
    return el("div", { class: "bars" }, row("before", before), row("after", after));
  }

  function metricRows(rows) {
    return el("div", { class: "metrics" }, rows.map(function (row) {
      var d = delta(row[1], row[2]);
      return el("div", { class: "metric" },
        el("span", { class: "name", text: row[0] }),
        bars(row[1], row[2]),
        el("span", { class: "delta " + d.cls, text: d.text }));
    }));
  }

  function renderMetrics(data) {
    var b = data.metrics.before, a = data.metrics.after;
    var node = panel("Circuit metrics", "grey = before, indigo = after · -O" + data.level, "half");
    node.appendChild(metricRows([
      ["quantum instructions", b.quantum_instructions, a.quantum_instructions],
      ["gates", b.gates, a.gates],
      ["1-qubit gates", b.one_qubit_gates, a.one_qubit_gates],
      ["2-qubit gates", b.two_qubit_gates, a.two_qubit_gates],
      ["depth", b.depth, a.depth],
      ["T gates", b.t_gates, a.t_gates],
      ["exact T-count", b.exact_t_count, a.exact_t_count],
      ["arbitrary rotations", b.arbitrary_rotations, a.arbitrary_rotations]
    ]));
    return node;
  }

  function renderHistogram(data) {
    var b = data.metrics.before.gate_histogram || {};
    var a = data.metrics.after.gate_histogram || {};
    var names = Object.keys(b).concat(Object.keys(a)).filter(function (v, i, arr) {
      return arr.indexOf(v) === i;
    }).sort();
    var node = panel("Gates by kind", "every operation in the module, before and after", "half");
    if (!names.length) {
      node.appendChild(el("p", { class: "empty", text: "no quantum instructions" }));
      return node;
    }
    node.appendChild(metricRows(names.map(function (name) {
      return [name, b[name] || 0, a[name] || 0];
    })));
    return node;
  }

  // --------------------------------------------------------------- circuits

  var GUTTER = 54, COLW = 46, ROWH = 40, PAD_TOP = 20, PAD_BOT = 12;

  function gateClass(op) {
    if (op.kind === "measure" || op.kind === "reset" || op.kind === "readout") return "meter";
    if (op.name === "t") return "tgate";
    if (op.shape === "pair" || op.name === "rx" || op.name === "ry" || op.name === "rz") return "rot";
    if (op.qubits.length > 1) return "two";
    return "clifford";
  }

  function boxStyle(cls) {
    return "fill:var(--" + cls + "-soft);stroke:var(--" + cls + ")";
  }

  function gateBox(x, y, op, label, cls) {
    var sub = op.angle_label;
    var w = Math.max(30, label.length * 8 + 12, sub ? sub.length * 6 + 10 : 0);
    var h = sub ? 30 : 26;
    var group = s("g", null,
      s("rect", {
        class: "gate-box", x: x - w / 2, y: y - h / 2, width: w, height: h,
        style: boxStyle(cls)
      }),
      s("text", {
        class: "gate-text", x: x, y: sub ? y - 5 : y,
        style: "fill:var(--" + cls + ")", text: label + (op.adjoint ? "†" : "")
      })
    );
    if (sub) {
      group.appendChild(s("text", {
        class: "gate-sub", x: x, y: y + 10,
        style: "fill:var(--" + cls + ")", text: sub
      }));
    }
    return { node: group, width: w };
  }

  function drawOp(op, x, wireY) {
    var cls = gateClass(op);
    var stroke = "stroke:var(--" + cls + ")";
    var group = s("g", null);
    var lanes = op.qubits;
    var ys = lanes.map(wireY);

    if (lanes.length > 1 && op.shape !== "box") {
      group.appendChild(s("line", {
        class: "link", x1: x, y1: Math.min.apply(null, ys), x2: x,
        y2: Math.max.apply(null, ys), style: stroke
      }));
    }

    function dot(y) {
      group.appendChild(s("circle", { cx: x, cy: y, r: 4.5, style: "fill:var(--" + cls + ")" }));
    }
    function ring(y) {
      group.appendChild(s("circle", {
        cx: x, cy: y, r: 10, style: "fill:var(--panel);" + stroke + ";stroke-width:1.6"
      }));
      group.appendChild(s("line", { x1: x - 10, y1: y, x2: x + 10, y2: y, style: stroke }));
      group.appendChild(s("line", { x1: x, y1: y - 10, x2: x, y2: y + 10, style: stroke }));
    }
    function cross(y) {
      var r = 6;
      group.appendChild(s("line", { x1: x - r, y1: y - r, x2: x + r, y2: y + r, style: stroke + ";stroke-width:1.8" }));
      group.appendChild(s("line", { x1: x - r, y1: y + r, x2: x + r, y2: y - r, style: stroke + ";stroke-width:1.8" }));
    }

    switch (op.shape) {
      case "control-target":
        dot(ys[0]); ring(ys[1]); break;
      case "control-control-target":
        dot(ys[0]); dot(ys[1]); ring(ys[2]); break;
      case "control-control":
        dot(ys[0]); dot(ys[1]); break;
      case "control-box":
        dot(ys[0]); group.appendChild(gateBox(x, ys[1], op, op.label, cls).node); break;
      case "swap":
        cross(ys[0]); cross(ys[1]); break;
      case "pair":
        ys.forEach(function (y, i) {
          var only = { angle_label: i === ys.length - 1 ? op.angle_label : null, adjoint: op.adjoint };
          group.appendChild(gateBox(x, y, only, op.label, cls).node);
        });
        break;
      default:
        var label = op.label || (op.name || "?").toUpperCase();
        if (op.kind === "measure" && op.results && op.results.length) {
          label = "M";
        }
        group.appendChild(gateBox(x, ys[0], op, label, cls).node);
    }
    if (op.text) group.appendChild(s("title", { text: op.text }));
    return group;
  }

  function drawBlock(block, wires) {
    var rows = Math.max(1, wires.length);
    var width = GUTTER + Math.max(block.columns, 1) * COLW + 20;
    var height = PAD_TOP + rows * ROWH + PAD_BOT;
    var root = s("svg", {
      width: width, height: height, viewBox: "0 0 " + width + " " + height,
      role: "img", "aria-label": "circuit diagram"
    });

    function wireY(i) { return PAD_TOP + i * ROWH + ROWH / 2; }

    wires.forEach(function (label, i) {
      root.appendChild(s("line", { class: "wire", x1: GUTTER - 12, y1: wireY(i), x2: width - 8, y2: wireY(i) }));
      root.appendChild(s("text", { class: "wire-label", x: 8, y: wireY(i) + 4, text: label }));
    });

    block.ops.forEach(function (op) {
      var x = GUTTER + op.column * COLW + COLW / 2;
      if (op.kind === "barrier") {
        root.appendChild(s("line", {
          class: "barrier-line", x1: x, y1: PAD_TOP - 4, x2: x, y2: PAD_TOP + rows * ROWH + 2
        }));
        root.appendChild(s("text", {
          class: "barrier-text", x: x, y: PAD_TOP - 8, "text-anchor": "middle", text: op.label
        }));
        if (op.text) root.appendChild(s("title", { text: op.text }));
        return;
      }
      root.appendChild(drawOp(op, x, wireY));
    });
    return root;
  }

  function renderCircuits(data) {
    var node = panel("Circuit", "hover a gate for its QIR instruction · T gates are amber");
    [["Before", data.circuit.before], ["After  -O" + data.level, data.circuit.after]].forEach(function (pair) {
      var circuit = pair[1];
      circuit.blocks.forEach(function (block, i) {
        var name = "@" + block.function + (block.label ? " / " + block.label : "");
        node.appendChild(el("div", { class: "circuit-head" },
          el("span", { class: "title", text: i === 0 ? pair[0] : "" }),
          el("span", { class: "meta", text: name + " · depth " + block.columns })));
        node.appendChild(el("div", { class: "scroller" }, drawBlock(block, circuit.wires)));
      });
    });
    node.appendChild(el("div", { class: "legend" },
      [["clifford", "Clifford 1-qubit"], ["tgate", "T / T†"], ["rot", "rotation"],
       ["two", "2-qubit"], ["meter", "measure / reset"]].map(function (pair) {
        return el("span", null,
          el("i", { style: "background:var(--" + pair[0] + "-soft);border:1px solid var(--" + pair[0] + ")" }),
          pair[1]);
      })));
    return node;
  }

  // ---------------------------------------------------------------- estimate

  function renderEstimate(data) {
    var b = data.estimate.before, a = data.estimate.after;
    var node = panel("Fault-tolerant resources",
      "surface code, error budget " + b.error_budget + ", " + b.qubit_params, "half");
    var rows = [
      ["algorithmic qubits", b.algorithmic_qubits, a.algorithmic_qubits],
      ["logical qubits", b.logical_qubits, a.logical_qubits],
      ["code distance", b.code_distance, a.code_distance],
      ["logical depth", b.logical_depth, a.logical_depth],
      ["T states", b.t_states, a.t_states],
      ["physical qubits", b.physical_qubits, a.physical_qubits],
      ["runtime (us)", b.runtime_ns / 1000, a.runtime_ns / 1000]
    ];
    node.appendChild(el("table", null,
      el("thead", null, el("tr", null,
        el("th", { text: "metric" }), el("th", { text: "before" }),
        el("th", { text: "after" }), el("th", { text: "change" }))),
      el("tbody", null, rows.map(function (row) {
        var d = delta(row[1], row[2]);
        return el("tr", null,
          el("td", { text: row[0] }),
          el("td", { class: "num", text: num(row[1]) }),
          el("td", { class: "num", text: num(row[2]) }),
          el("td", { class: "num delta " + d.cls, text: d.text }));
      }))));
    (b.model_notes || []).forEach(function (note) {
      node.appendChild(el("p", { class: "hint", style: "margin-top:12px", text: note }));
    });
    return node;
  }

  // ------------------------------------------------------------------ trace

  function renderTrace(data) {
    var node = panel("Rewrite trace",
      data.rewrites.length + " recorded identities · " +
      data.pipeline.total_rewrites + " rewrites over " + data.pipeline.iterations + " iterations", "half");
    if (!data.rewrites.length) {
      node.appendChild(el("p", { class: "empty", text: "nothing to rewrite at this level" }));
      return node;
    }
    node.appendChild(el("ul", { class: "trace" }, data.rewrites.map(function (entry) {
      return el("li", null,
        el("span", { class: "pill " + entry.pass, text: entry.pass }),
        el("span", { class: "note", text: entry.note }));
    })));
    return node;
  }

  // ------------------------------------------------------------------- diff

  function renderDiff(data) {
    var changed = data.diff.filter(function (row) { return row.kind !== " "; }).length;
    var node = panel("QIR diff", changed + " changed lines · everything else is emitted byte for byte");
    node.appendChild(el("div", { class: "diff" }, data.diff.map(function (row) {
      var cls = row.kind === "+" ? "add" : row.kind === "-" ? "del" : "ctx";
      return el("div", { class: cls, text: row.kind + " " + row.text });
    })));
    return node;
  }

  // ----------------------------------------------------------------- render

  function render(data) {
    var app = document.getElementById("app");
    app.textContent = "";
    app.appendChild(renderProof(data));
    app.appendChild(renderMetrics(data));
    app.appendChild(renderEstimate(data));
    app.appendChild(renderCircuits(data));
    app.appendChild(renderTrace(data));
    app.appendChild(renderHistogram(data));
    app.appendChild(renderDiff(data));

    document.getElementById("cmd").textContent =
      "qizil " + data.name + " -O" + data.level +
      (data.options.gateset === "strict" ? " --gateset strict" : "") +
      (data.options.preserve_global_phase ? " --preserve-global-phase" : "") +
      " -o optimized.ll --verify";
    document.getElementById("footnote").textContent =
      "Resource model after arXiv:2211.07629. Equivalence checked with the " +
      ((data.verification && data.verification.backend) || "reference") +
      " simulator. Qizil " + (data.version || "");
  }

  function fail(message) {
    var box = document.getElementById("status");
    box.hidden = false;
    box.textContent = message;
  }

  // ------------------------------------------------------------ interactive

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      if (!response.ok) return response.text().then(function (t) { throw new Error(t); });
      return response.json();
    });
  }

  function run() {
    var button = document.getElementById("run");
    button.disabled = true;
    document.getElementById("status").hidden = true;
    post("/api/optimize", {
      source: state.source, name: state.name, level: state.level,
      gateset: state.gateset, preserve_global_phase: state.phase, verify: state.verify
    }).then(function (data) {
      render(data);
    }).catch(function (error) {
      fail("optimization failed: " + error.message);
    }).then(function () {
      button.disabled = false;
    });
  }

  function buildLevels() {
    var box = document.getElementById("level");
    [0, 1, 2, 3].forEach(function (level) {
      var button = el("button", {
        type: "button", text: "-O" + level,
        "aria-pressed": String(level === state.level)
      });
      button.addEventListener("click", function () {
        state.level = level;
        Array.prototype.forEach.call(box.children, function (child) {
          child.setAttribute("aria-pressed", String(child === button));
        });
        run();
      });
      box.appendChild(button);
    });
  }

  function boot() {
    if (STATIC) {
      document.getElementById("controls").hidden = true;
      render(STATIC);
      return;
    }
    document.getElementById("controls").hidden = false;
    buildLevels();

    var picker = document.getElementById("example");
    document.getElementById("run").addEventListener("click", run);
    document.getElementById("gateset").addEventListener("change", function (e) {
      state.gateset = e.target.value; run();
    });
    document.getElementById("phase").addEventListener("change", function (e) {
      state.phase = e.target.checked; run();
    });
    document.getElementById("verify").addEventListener("change", function (e) {
      state.verify = e.target.checked; run();
    });
    document.getElementById("file").addEventListener("change", function (e) {
      var file = e.target.files && e.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        state.source = String(reader.result);
        state.name = file.name;
        run();
      };
      reader.readAsText(file);
    });
    picker.addEventListener("change", function () {
      var chosen = EXAMPLES[picker.selectedIndex];
      if (!chosen) return;
      state.source = chosen.source;
      state.name = chosen.name;
      run();
    });

    fetch("/api/examples").then(function (r) { return r.json(); }).then(function (data) {
      EXAMPLES = data.examples || [];
      EXAMPLES.forEach(function (example) {
        picker.appendChild(el("option", { value: example.name, text: example.name }));
      });
      var preferred = EXAMPLES.findIndex(function (e) { return e.name.indexOf("trotter") === 0; });
      picker.selectedIndex = preferred >= 0 ? preferred : 0;
      var chosen = EXAMPLES[picker.selectedIndex];
      if (chosen) {
        state.source = chosen.source;
        state.name = chosen.name;
        run();
      }
    }).catch(function (error) {
      fail("could not load examples: " + error.message);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
