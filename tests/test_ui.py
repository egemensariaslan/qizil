"""The browser UI and the standalone HTML report."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.request

import pytest

from conftest import EXAMPLES, make_ir
from qizil.analysis.circuit import describe, pi_label
from qizil.api import parse
from qizil.cli import main
from qizil.ui.payload import build, list_examples
from qizil.ui.server import make_server, render_page

BELL = os.path.join(EXAMPLES, "bell_redundant.ll")


# --------------------------------------------------------------------------
# circuit description
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "angle,expected",
    [
        (0.0, "0"),
        (math.pi, "pi"),
        (-math.pi, "-pi"),
        (math.pi / 2, "pi/2"),
        (3 * math.pi / 4, "3pi/4"),
        (-math.pi / 4, "-pi/4"),
        (2 * math.pi, "2pi"),
        (0.37, "0.37"),
        (None, None),
    ],
)
def test_pi_label(angle, expected):
    assert pi_label(angle) == expected


def test_describe_lays_gates_out_in_columns():
    module = parse(
        make_ir(
            [
                ("h", "body", None, [0]),
                ("h", "body", None, [1]),
                ("cnot", "body", None, [0, 1]),
                ("rz", "body", math.pi / 4, [1]),
            ],
            num_qubits=2,
        )
    )
    data = describe(module)
    assert data["wires"] == ["q0", "q1"]
    ops = data["blocks"][0]["ops"]
    # Independent gates share a column; the CNOT waits for both.
    assert [op["column"] for op in ops] == [0, 0, 1, 2]
    assert ops[2]["shape"] == "control-target"
    assert ops[2]["qubits"] == [0, 1]
    assert ops[3]["angle_label"] == "pi/4"


def test_describe_marks_barriers():
    module = parse(
        """%Qubit = type opaque

define void @main() {
entry:
  call void @__quantum__qis__h__body(%Qubit* null)
  call void @__quantum__rt__qubit_release(%Qubit* null)
  ret void
}

declare void @__quantum__qis__h__body(%Qubit*)
declare void @__quantum__rt__qubit_release(%Qubit*)
"""
    )
    ops = describe(module)["blocks"][0]["ops"]
    assert [op["kind"] for op in ops] == ["gate", "barrier"]
    assert ops[1]["label"] == "rt.qubit_release"


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------


def test_payload_has_everything_the_ui_draws():
    with open(BELL, encoding="utf-8") as fh:
        data = build(fh.read(), name="bell_redundant.ll", level=3)
    for key in (
        "metrics", "circuit", "pipeline", "rewrites", "verification",
        "estimate", "ir", "diff", "global_phase", "llvm",
    ):
        assert key in data, key
    assert data["metrics"]["before"]["gates"] > data["metrics"]["after"]["gates"]
    assert data["verification"]["equivalent"] is True
    assert data["rewrites"] and "=" in data["rewrites"][0]["note"]
    assert any(row["kind"] == "-" for row in data["diff"])
    assert json.dumps(data)  # must be JSON-serializable end to end


def test_payload_is_honest_about_a_no_op_level():
    with open(BELL, encoding="utf-8") as fh:
        data = build(fh.read(), level=0)
    assert data["pipeline"]["total_rewrites"] == 0
    assert data["ir"]["before"] == data["ir"]["after"]
    assert not any(row["kind"] != " " for row in data["diff"])


def test_examples_are_discoverable_from_a_checkout():
    names = [e["name"] for e in list_examples()]
    assert "bell_redundant.ll" in names
    assert all(e["source"].strip() for e in list_examples())


# --------------------------------------------------------------------------
# page assembly
# --------------------------------------------------------------------------


def test_page_is_self_contained():
    page = render_page({"name": "x"})
    assert "window.QIZIL_DATA" in page
    assert "<style>" in page and "--accent" in page  # CSS inlined
    assert "function renderProof" in page  # JS inlined
    # No external resources: a report must render offline, forever.
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', page)
    assert "cdn" not in page.lower()


def test_page_escapes_closing_tags_in_data():
    page = render_page({"name": "</script><script>alert(1)</script>"})
    assert "</script><script>alert" not in page


def test_report_command_writes_a_standalone_file(tmp_path, capsys):
    out = tmp_path / "report.html"
    assert main(["report", BELL, "-O3", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "window.QIZIL_DATA" in html  # the data travels with the page
    assert '"equivalent": true' in html
    assert '"backend": "' in html
    assert "self-contained" in capsys.readouterr().out


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------


@pytest.fixture
def server():
    httpd = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def _post(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_server_serves_the_app(server):
    status, body = _get(server + "/")
    assert status == 200
    assert "Qizil" in body


def test_server_lists_examples(server):
    status, body = _get(server + "/api/examples")
    assert status == 200
    assert any(e["name"] == "bell_redundant.ll" for e in json.loads(body)["examples"])


def test_server_optimizes(server):
    with open(BELL, encoding="utf-8") as fh:
        source = fh.read()
    status, data = _post(server + "/api/optimize", {"source": source, "level": 3})
    assert status == 200
    assert data["metrics"]["after"]["t_gates"] == 0
    assert data["verification"]["equivalent"] is True


def test_server_rejects_an_empty_request(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(server + "/api/optimize", {})
    assert excinfo.value.code == 400


def test_server_reports_a_bad_module(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(server + "/api/optimize", {"source": "\x00\x00 not ir"})
    assert excinfo.value.code == 400


def test_server_never_reads_source_as_a_filesystem_path(server, tmp_path):
    """``source`` must always be treated as literal QIR text.

    A network-facing handler that let a client's string be interpreted as a
    path to open on the server's disk would be a local-file-read primitive.
    qizil.api.optimize has a path-sniffing heuristic for CLI convenience
    (`qizil circuit.ll`); payload.build must never let that heuristic run on
    text that arrived over HTTP. This asserts a real, readable file's path,
    given as ``source``, is rejected as non-QIR text rather than having its
    contents echoed back.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("super-secret-file-contents\n")

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(server + "/api/optimize", {"source": str(secret)})
    assert excinfo.value.code == 400
    body = json.loads(excinfo.value.read())
    assert "super-secret" not in json.dumps(body)


def test_server_gives_a_clear_error_for_non_qir_text(server):
    status, data = None, None
    try:
        _post(server + "/api/optimize", {"source": "hello, this is just some text"})
    except urllib.error.HTTPError as exc:
        status = exc.code
        data = json.loads(exc.read())
    assert status == 400
    assert "does not look like a QIR module" in data["error"]


def test_server_404s_unknown_paths(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(server + "/nope")
    assert excinfo.value.code == 404
