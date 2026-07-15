"""End-to-end CLI behaviour."""

from __future__ import annotations

import json
import os

import pytest

from conftest import EXAMPLES, make_ir
from qizil.cli import main

BELL = os.path.join(EXAMPLES, "bell_redundant.ll")


def test_headline_invocation(tmp_path, capsys):
    out = tmp_path / "out.ll"
    assert main([BELL, "-O2", "-o", str(out)]) == 0
    text = out.read_text()
    assert "@__quantum__qis__cnot__body" in text
    assert "@__quantum__qis__x__body(%Qubit* inttoptr" not in text
    assert "quantum instructions" in capsys.readouterr().out


def test_optimize_to_stdout(capsys):
    assert main([BELL, "-O2", "--quiet"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("; ModuleID")
    assert captured.err == ""


def test_optimization_levels_differ(tmp_path):
    outputs = {}
    for level in (0, 1, 2, 3):
        path = tmp_path / f"o{level}.ll"
        assert main([BELL, f"-O{level}", "-o", str(path), "--quiet"]) == 0
        outputs[level] = path.read_text()
    assert outputs[0].count("call void @__quantum__qis__") > outputs[1].count(
        "call void @__quantum__qis__"
    )
    assert outputs[1].count("call void @__quantum__qis__") > outputs[2].count(
        "call void @__quantum__qis__"
    )
    assert outputs[2] == outputs[3]


def test_json_report(tmp_path, capsys):
    report = tmp_path / "report.json"
    out = tmp_path / "out.ll"
    assert main([BELL, "-O2", "-o", str(out), "--report", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["before"]["gates"] > data["after"]["gates"]
    assert data["level"] == 2
    assert any(p["rewrites"] for p in data["pipeline"]["passes"])


def test_json_stdout(capsys):
    assert main([BELL, "-O2", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["after"]["t_gates"] == 0


def test_verify_flag(capsys):
    assert main([BELL, "-O3", "--verify", "--quiet"]) == 0


def test_stats_command(capsys):
    assert main(["stats", BELL, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["qubits"] == 2
    assert data["gate_histogram"]["h"] == 3


def test_estimate_command(capsys):
    assert main(["estimate", BELL, "-O2", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["before"]["t_states"] > data["after"]["t_states"]
    assert data["comparison"]["physical_qubits"]["percent"] < 0


def test_estimate_single_module(capsys):
    assert main(["estimate", BELL, "--no-optimize"]) == 0
    assert "physical_qubits" in capsys.readouterr().out


def test_verify_command(tmp_path, capsys):
    out = tmp_path / "out.ll"
    main([BELL, "-O3", "-o", str(out), "--quiet"])
    assert main(["verify", BELL, str(out)]) == 0
    assert "EQUIVALENT" in capsys.readouterr().out


def test_verify_command_detects_a_difference(tmp_path, capsys):
    a = tmp_path / "a.ll"
    b = tmp_path / "b.ll"
    a.write_text(make_ir([("t", "body", None, [0])] * 2, 1))
    b.write_text(make_ir([("t", "body", None, [0])], 1))
    assert main(["verify", str(a), str(b)]) == 2
    assert "NOT EQUIVALENT" in capsys.readouterr().out


def test_dag_command(capsys):
    assert main(["dag", BELL]) == 0
    out = capsys.readouterr().out
    assert "cnot(qubit0,qubit1)" in out
    assert "depth" in out


def test_dag_dot_output(capsys):
    assert main(["dag", BELL, "--dot"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("digraph")
    assert "->" in out


def test_passes_command(capsys):
    assert main(["passes"]) == 0
    out = capsys.readouterr().out
    assert "clifford-t" in out
    assert "-O2" in out


def test_pass_selection(tmp_path):
    out = tmp_path / "out.ll"
    assert main([BELL, "--passes", "cancel", "-o", str(out), "--quiet"]) == 0
    text = out.read_text()
    assert "@__quantum__qis__t__body" in text  # clifford-t never ran


def test_unknown_pass_is_reported(capsys):
    assert main([BELL, "--passes", "nope", "--quiet"]) == 1
    assert "unknown pass" in capsys.readouterr().err


def test_bitcode_roundtrip(tmp_path, capsys):
    pytest.importorskip("pyqir")
    from qizil.ir.bitcode import to_bitcode
    from qizil.ir.parser import parse_file

    bc = tmp_path / "input.bc"
    bc.write_bytes(to_bitcode(parse_file(BELL)))
    out = tmp_path / "out.bc"
    assert main([str(bc), "-O2", "-o", str(out), "--quiet"]) == 0
    assert out.read_bytes()[:4] == b"BC\xc0\xde"


def test_stdin_input(monkeypatch, capsys):
    import io

    with open(BELL, encoding="utf-8") as fh:
        monkeypatch.setattr("sys.stdin", io.StringIO(fh.read()))
    assert main(["-", "-O2", "--quiet"]) == 0
    assert capsys.readouterr().out.startswith("; ModuleID")


def test_missing_file_is_reported(capsys):
    assert main(["does-not-exist.ll", "-O2"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_flags_may_precede_the_input(capsys):
    assert main(["--json", BELL]) == 0
    assert json.loads(capsys.readouterr().out)["level"] == 2


def test_no_arguments_prints_help(capsys):
    assert main([]) == 1
    assert "usage" in capsys.readouterr().out
