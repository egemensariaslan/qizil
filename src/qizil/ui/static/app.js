/* Qizil UI — renders an optimization report.

   No frameworks and no network beyond this app's own API. Two modes:
     - interactive: talks to the local `qizil ui` server
     - static:      renders window.QIZIL_DATA embedded by `qizil report`
   See app.css for the design system these classes belong to.                */

(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var STATIC = window.QIZIL_DATA || null;
  var EXAMPLES = [];
  var state = {
    level: 3, gateset: "auto", phase: false, verify: true, source: null, name: ""
  };

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
    if (value === null || value === undefined) return "—";
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
    if (before === after) return { text: "—", cls: "" };
    if (!before) return { text: "new", cls: after > before ? "bad" : "good" };
    var change = ((after - before) / before) * 100;
    return {
      text: (change > 0 ? "+" : "−") + Math.abs(change).toFixed(1) + "%",
      cls: change < 0 ? "good" : "bad"
    };
  }

  var order = 0;

  function cell(title, meta, span) {
    order += 1;
    var node = el("section", {
      class: "cell" + (span ? " s" + span : ""),
      style: "animation-delay:" + Math.min(order * 45, 320) + "ms"
    },
      el("header", { class: "cell-head" },
        el("span", { class: "idx", text: ("0" + order).slice(-2) }),
        el("h2", { text: title }),
        meta ? el("span", { class: "meta", text: meta }) : null));
    return node;
  }

  // ------------------------------------------------------------------ hero

  function heroStat(label, before, after, suffix) {
    var d = delta(before, after);
    return el("div", { class: "stat" },
      el("span", { class: "k", text: label }),
      el("span", { class: "v" },
        el("span", { class: "from", text: num(before) }),
        el("span", { class: "arrow", text: "→" }),
        el("span", { class: "to", text: num(after) }),
        suffix ? el("span", { class: "unit", text: suffix }) : null),
      el("span", { class: "d " + d.cls, text: d.text }));
  }

  function renderHero(data) {
    var b = data.metrics.before, a = data.metrics.after;
    var eb = data.estimate.before, ea = data.estimate.after;
    return el("div", { class: "hero" },
      heroStat("gates", b.gates, a.gates),
      heroStat("circuit depth", b.depth, a.depth),
      heroStat("T-count", b.exact_t_count, a.exact_t_count),
      heroStat("arbitrary rotations", b.arbitrary_rotations, a.arbitrary_rotations),
      heroStat("physical qubits", eb.physical_qubits, ea.physical_qubits),
      heroStat("runtime", eb.runtime_ns / 1000, ea.runtime_ns / 1000, "µs"));
  }

  // ----------------------------------------------------------------- proof

  function fact(key, value) {
    return el("div", { class: "fact" },
      el("span", { class: "fk", text: key }),
      el("span", { class: "fv", text: String(value) }));
  }

  function renderProof(data) {
    var v = data.verification;
    var facts = [];
    var cls, seal, claim;

    if (!v) {
      cls = "warn"; seal = "○"; claim = "not checked";
    } else if (v.equivalent) {
      cls = "ok"; seal = "✓"; claim = "U_out ≡ U_in";
      facts.push(fact("max error", v.max_error.toExponential(2)));
      facts.push(fact("segments", v.segments_checked));
      facts.push(fact("simulator", v.backend));
    } else if (!v.available) {
      cls = "warn"; seal = "○";
      claim = (v.messages || []).join("; ") || "check unavailable";
    } else {
      cls = "fail"; seal = "✗";
      claim = "U_out ≠ U_in — " + (v.messages || []).join("; ");
    }
    if (data.global_phase) {
      facts.push(fact("global phase", data.global_phase.toFixed(6) + " rad"));
    }
    if (data.llvm && data.llvm.checked) {
      facts.push(fact("llvm", data.llvm.diagnostic ? "failed" : "passed"));
    }

    return el("div", { class: "proof " + cls },
      el("span", { class: "seal", text: seal }),
      el("span", { class: "claim", text: claim }),
      el("div", { class: "facts" }, facts));
  }

  // ------------------------------------------------------------------ bars

  var pending = [];

  function bars(before, after) {
    var scale = Math.max(before, after, 1);
    function line(kind, value) {
      var fill = el("span", { class: "fill" });
      pending.push([fill, (value / scale) * 100]);
      return el("div", { class: "bar " + kind },
        el("span", { class: "track" }, fill),
        el("span", { class: "n", text: num(value) }));
    }
    return el("div", { class: "rb" }, line("a", before), line("b", after));
  }

  function flushBars() {
    var queued = pending;
    pending = [];
    requestAnimationFrame(function () {
      queued.forEach(function (entry) { entry[0].style.width = entry[1] + "%"; });
    });
  }

  function rows(list) {
    return el("div", { class: "rows" }, list.map(function (row) {
      var d = delta(row[1], row[2]);
      return el("div", { class: "row" },
        el("span", { class: "rk", text: row[0] }),
        bars(row[1], row[2]),
        el("span", { class: "chg " + d.cls, text: d.text }));
    }));
  }

  function renderMetrics(data) {
    var b = data.metrics.before, a = data.metrics.after;
    var node = cell("Circuit metrics", null, 5);
    node.appendChild(rows([
      ["instructions", b.quantum_instructions, a.quantum_instructions],
      ["gates", b.gates, a.gates],
      ["1-qubit", b.one_qubit_gates, a.one_qubit_gates],
      ["2-qubit", b.two_qubit_gates, a.two_qubit_gates],
      ["depth", b.depth, a.depth],
      ["T gates", b.t_gates, a.t_gates],
      ["exact T-count", b.exact_t_count, a.exact_t_count],
      ["rotations", b.arbitrary_rotations, a.arbitrary_rotations]
    ]));
    return node;
  }

  function renderHistogram(data) {
    var b = data.metrics.before.gate_histogram || {};
    var a = data.metrics.after.gate_histogram || {};
    var names = Object.keys(b).concat(Object.keys(a)).filter(function (v, i, arr) {
      return arr.indexOf(v) === i;
    }).sort();
    var node = cell("Gates by kind", null, 6);
    if (!names.length) {
      node.appendChild(el("p", { class: "void", text: "no quantum instructions" }));
      return node;
    }
    node.appendChild(rows(names.map(function (name) {
      return [name, b[name] || 0, a[name] || 0];
    })));
    return node;
  }

  // -------------------------------------------------------------- circuits

  var GUTTER = 56, COLW = 46, ROWH = 40, PAD_TOP = 16, PAD_BOT = 14;

  function gateClass(op) {
    if (op.kind === "measure" || op.kind === "reset" || op.kind === "readout") return "meter";
    if (op.name === "t") return "tgate";
    if (op.shape === "pair" || op.name === "rx" || op.name === "ry" || op.name === "rz") return "rot";
    if (op.qubits.length > 1) return "two";
    return "clifford";
  }

  function gateBox(x, y, op, label, cls) {
    var sub = op.angle_label;
    var w = Math.max(28, label.length * 8 + 12, sub ? sub.length * 6 + 10 : 0);
    var h = sub ? 30 : 26;
    var group = s("g", null,
      s("rect", {
        class: "gate-box", x: x - w / 2, y: y - h / 2, width: w, height: h,
        style: "fill:var(--" + cls + "-bg);stroke:var(--" + cls + ")"
      }),
      s("text", {
        class: "gate-text", x: x, y: sub ? y - 5 : y,
        style: "fill:var(--" + cls + ")",
        text: label + (op.adjoint ? "†" : "")
      }));
    if (sub) {
      group.appendChild(s("text", {
        class: "gate-sub", x: x, y: y + 10,
        style: "fill:var(--" + cls + ")", text: sub
      }));
    }
    return group;
  }

  function drawOp(op, x, wireY) {
    var cls = gateClass(op);
    var stroke = "stroke:var(--" + cls + ")";
    var group = s("g", { class: "op" });
    var ys = op.qubits.map(wireY);

    if (op.qubits.length > 1 && op.shape !== "box") {
      group.appendChild(s("line", {
        class: "link", x1: x, y1: Math.min.apply(null, ys),
        x2: x, y2: Math.max.apply(null, ys), style: stroke
      }));
    }

    function dot(y) {
      group.appendChild(s("circle", { cx: x, cy: y, r: 4.5, style: "fill:var(--" + cls + ")" }));
    }
    function ring(y) {
      group.appendChild(s("circle", {
        cx: x, cy: y, r: 10,
        style: "fill:var(--bg-3);" + stroke + ";stroke-width:1.5"
      }));
      group.appendChild(s("line", { x1: x - 10, y1: y, x2: x + 10, y2: y, style: stroke }));
      group.appendChild(s("line", { x1: x, y1: y - 10, x2: x, y2: y + 10, style: stroke }));
    }
    function cross(y) {
      var r = 6;
      group.appendChild(s("line", {
        x1: x - r, y1: y - r, x2: x + r, y2: y + r, style: stroke + ";stroke-width:1.8"
      }));
      group.appendChild(s("line", {
        x1: x - r, y1: y + r, x2: x + r, y2: y - r, style: stroke + ";stroke-width:1.8"
      }));
    }

    switch (op.shape) {
      case "control-target": dot(ys[0]); ring(ys[1]); break;
      case "control-control-target": dot(ys[0]); dot(ys[1]); ring(ys[2]); break;
      case "control-control": dot(ys[0]); dot(ys[1]); break;
      case "control-box":
        dot(ys[0]);
        group.appendChild(gateBox(x, ys[1], op, op.label, cls));
        break;
      case "swap": cross(ys[0]); cross(ys[1]); break;
      case "pair":
        ys.forEach(function (y, i) {
          var slice = {
            angle_label: i === ys.length - 1 ? op.angle_label : null,
            adjoint: op.adjoint
          };
          group.appendChild(gateBox(x, y, slice, op.label, cls));
        });
        break;
      default:
        group.appendChild(gateBox(x, ys[0], op, op.label || "?", cls));
    }
    if (op.text) group.appendChild(s("title", { text: op.text }));
    return group;
  }

  function drawBlock(block, wires) {
    var lanes = Math.max(1, wires.length);
    var width = GUTTER + Math.max(block.columns, 1) * COLW + 22;
    var height = PAD_TOP + lanes * ROWH + PAD_BOT;
    var root = s("svg", {
      width: width, height: height, viewBox: "0 0 " + width + " " + height,
      role: "img", "aria-label": "quantum circuit diagram"
    });

    function wireY(i) { return PAD_TOP + i * ROWH + ROWH / 2; }

    wires.forEach(function (label, i) {
      root.appendChild(s("line", {
        class: "wire", x1: GUTTER - 14, y1: wireY(i), x2: width - 8, y2: wireY(i)
      }));
      root.appendChild(s("text", {
        class: "wire-label", x: 10, y: wireY(i) + 4, text: label
      }));
    });

    block.ops.forEach(function (op) {
      var x = GUTTER + op.column * COLW + COLW / 2;
      if (op.kind === "barrier") {
        root.appendChild(s("line", {
          class: "barrier-line", x1: x, y1: PAD_TOP - 2, x2: x, y2: PAD_TOP + lanes * ROWH
        }));
        root.appendChild(s("text", {
          class: "barrier-text", x: x, y: PAD_TOP + lanes * ROWH + 10,
          "text-anchor": "middle", text: op.label
        }));
        return;
      }
      root.appendChild(drawOp(op, x, wireY));
    });
    return root;
  }

  function stage(label, isAfter, block, wires) {
    return el("div", { class: "stage" },
      el("span", { class: "stage-tag" + (isAfter ? " now" : "") },
        label,
        el("em", { text: "@" + block.function + (block.label ? " · " + block.label : "") }),
        el("em", { text: block.columns + (block.columns === 1 ? " column" : " columns") })),
      el("div", { class: "scroller" }, drawBlock(block, wires)));
  }

  function renderCircuit(data) {
    var node = cell("Circuit");
    [["Before", false, data.circuit.before], ["After −O" + data.level, true, data.circuit.after]]
      .forEach(function (pair) {
        pair[2].blocks.forEach(function (block) {
          node.appendChild(stage(pair[0], pair[1], block, pair[2].wires));
        });
      });
    node.appendChild(el("div", { class: "legend" },
      [["clifford", "Clifford"], ["tgate", "T / T†"], ["rot", "rotation"],
       ["two", "2-qubit"], ["meter", "measure"]].map(function (pair) {
        return el("span", null,
          el("i", {
            style: "background:var(--" + pair[0] + "-bg);border:1px solid var(--" + pair[0] + ")"
          }),
          pair[1]);
      })));
    return node;
  }

  // -------------------------------------------------------------- estimate

  function renderEstimate(data) {
    var b = data.estimate.before, a = data.estimate.after;
    var node = cell("Fault-tolerant resources",
      "surface code · ε " + b.error_budget + " · " + b.qubit_params, 7);
    var list = [
      ["algorithmic qubits", b.algorithmic_qubits, a.algorithmic_qubits],
      ["logical qubits", b.logical_qubits, a.logical_qubits],
      ["code distance", b.code_distance, a.code_distance],
      ["logical depth", b.logical_depth, a.logical_depth],
      ["T states", b.t_states, a.t_states],
      ["physical qubits", b.physical_qubits, a.physical_qubits],
      ["runtime (µs)", b.runtime_ns / 1000, a.runtime_ns / 1000]
    ];
    node.appendChild(el("table", null,
      el("thead", null, el("tr", null,
        el("th", { text: "metric" }), el("th", { text: "before" }),
        el("th", { text: "after" }), el("th", { text: "Δ" }))),
      el("tbody", null, list.map(function (row) {
        var d = delta(row[1], row[2]);
        return el("tr", null,
          el("td", { text: row[0] }),
          el("td", { class: "n", text: num(row[1]) }),
          el("td", { class: "n", text: num(row[2]) }),
          el("td", { class: "n chg " + d.cls, text: d.text }));
      }))));
    (b.model_notes || []).forEach(function (note) {
      node.appendChild(el("p", {
        class: "void", style: "margin:14px 0 0;line-height:1.6", text: note
      }));
    });
    return node;
  }

  // ----------------------------------------------------------------- trace

  function renderTrace(data) {
    var node = cell("Rewrite trace",
      data.pipeline.total_rewrites + " rewrites · " + data.pipeline.iterations + " passes", 6);
    if (!data.rewrites.length) {
      node.appendChild(el("p", { class: "void", text: "nothing to rewrite at this level" }));
      return node;
    }
    node.appendChild(el("ul", { class: "trace" }, data.rewrites.map(function (entry) {
      return el("li", null,
        el("span", { class: "tag " + entry.pass, text: entry.pass }),
        el("span", { class: "note", text: entry.note }));
    })));
    return node;
  }

  // ------------------------------------------------------------------ diff

  function renderDiff(data) {
    var changed = data.diff.filter(function (row) { return row.kind !== " "; }).length;
    var node = cell("QIR diff", changed + " changed lines");
    node.appendChild(el("div", { class: "diff" }, data.diff.map(function (row) {
      var cls = row.kind === "+" ? "add" : row.kind === "-" ? "del" : "ctx";
      return el("div", { class: cls, text: row.kind + " " + row.text });
    })));
    return node;
  }

  // ---------------------------------------------------------------- render

  function render(data) {
    var app = document.getElementById("app");
    app.textContent = "";
    order = 0;
    pending = [];

    app.appendChild(renderHero(data));
    app.appendChild(renderProof(data));
    app.appendChild(renderMetrics(data));
    app.appendChild(renderEstimate(data));
    app.appendChild(renderCircuit(data));
    app.appendChild(renderTrace(data));
    app.appendChild(renderHistogram(data));
    app.appendChild(renderDiff(data));
    flushBars();

    document.getElementById("cmd").textContent =
      "qizil " + data.name + " -O" + data.level +
      (data.options.gateset === "strict" ? " --gateset strict" : "") +
      (data.options.preserve_global_phase ? " --preserve-global-phase" : "") +
      " -o optimized.ll --verify";
    document.getElementById("footnote").textContent =
      "QIZIL " + (data.version || "") +
      "  ·  resource model after arXiv:2211.07629" +
      "  ·  equivalence checked with the " +
      ((data.verification && data.verification.backend) || "reference") + " simulator";

    announce(summarize(data));
  }

  function summarize(data) {
    var v = data.verification;
    var verdict = !v
      ? "equivalence not checked"
      : v.equivalent
        ? "verified equivalent"
        : v.available
          ? "NOT equivalent"
          : "equivalence unavailable";
    var b = data.metrics.before, a = data.metrics.after;
    return (
      "Optimization complete at level " + data.level + ". " + verdict + ". " +
      "Gates reduced from " + b.gates + " to " + a.gates + "."
    );
  }

  function announce(message) {
    var region = document.getElementById("announce");
    if (region) region.textContent = message;
  }

  function fail(message) {
    var box = document.getElementById("status");
    box.hidden = false;
    box.textContent = message;
  }

  // ----------------------------------------------------------------- theme

  function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var label = document.getElementById("theme-label");
    if (label) label.textContent = theme === "dark" ? "LIGHT" : "DARK";
    try { localStorage.setItem("qizil-theme", theme); } catch (e) { /* private mode */ }
  }

  function initTheme() {
    var stored = null;
    try { stored = localStorage.getItem("qizil-theme"); } catch (e) { /* ignore */ }
    setTheme(stored === "light" ? "light" : "dark");
    var button = document.getElementById("theme");
    if (!button) return;
    button.addEventListener("click", function () {
      var now = document.documentElement.getAttribute("data-theme");
      setTheme(now === "dark" ? "light" : "dark");
    });
  }

  // ----------------------------------------------------------- interactive

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      if (!response.ok) {
        return response.json()
          .catch(function () { return { error: response.statusText }; })
          .then(function (data) { throw new Error(data.error || "request failed"); });
      }
      return response.json();
    });
  }

  function run() {
    var button = document.getElementById("run");
    button.disabled = true;
    document.body.classList.add("busy");
    document.getElementById("status").hidden = true;
    post("/api/optimize", {
      source: state.source, name: state.name, level: state.level,
      gateset: state.gateset, preserve_global_phase: state.phase, verify: state.verify
    }).then(render).catch(function (error) {
      fail("optimization failed — " + error.message);
    }).then(function () {
      button.disabled = false;
      document.body.classList.remove("busy");
    });
  }

  function buildLevels() {
    var box = document.getElementById("level");
    [0, 1, 2, 3].forEach(function (level) {
      // role="radio" + aria-checked, not aria-pressed: these four buttons
      // are mutually exclusive, matching the radiogroup role already on
      // their container (see index.html) -- a toggle-button pattern here
      // would tell assistive tech something different from what's true.
      var button = el("button", {
        type: "button", text: "−O" + level, role: "radio",
        "aria-checked": String(level === state.level)
      });
      button.addEventListener("click", function () {
        state.level = level;
        Array.prototype.forEach.call(box.children, function (child) {
          child.setAttribute("aria-checked", String(child === button));
        });
        run();
      });
      box.appendChild(button);
    });
  }

  function boot() {
    initTheme();
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
      var preferred = EXAMPLES.findIndex(function (e) {
        return e.name.indexOf("trotter") === 0;
      });
      picker.selectedIndex = preferred >= 0 ? preferred : 0;
      var chosen = EXAMPLES[picker.selectedIndex];
      if (chosen) {
        state.source = chosen.source;
        state.name = chosen.name;
        run();
      }
    }).catch(function (error) {
      fail("could not load examples — " + error.message);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
