"""The graph-freshness gate must catch stale locations, and only those."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
CHECKER = REPO / "scripts" / "checks" / "graph_freshness.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_graph_freshness_gate", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_graph(tmp_path: Path, nodes: list[dict]) -> Path:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    return graph_path


def _write_source(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestCheckGraph:
    def test_a_current_location_is_clean(self, tmp_path: Path) -> None:
        checker = _load_checker()
        _write_source(tmp_path, "src/valid.py", "def ok() -> None:\n    pass\n")
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "ok()",
                    "file_type": "code",
                    "source_file": "src/valid.py",
                    "source_location": "L1",
                }
            ],
        )
        assert checker.check_graph(tmp_path, graph) == []

    def test_an_empty_module_may_claim_line_one(self, tmp_path: Path) -> None:
        checker = _load_checker()
        _write_source(tmp_path, "src/empty.py", "")
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "empty.py",
                    "file_type": "code",
                    "source_file": "src/empty.py",
                    "source_location": "L1",
                }
            ],
        )
        assert checker.check_graph(tmp_path, graph) == []

    def test_a_line_beyond_the_file_is_stale(self, tmp_path: Path) -> None:
        checker = _load_checker()
        _write_source(tmp_path, "src/stale.py", "def present() -> None:\n    pass\n")
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "present()",
                    "file_type": "code",
                    "source_file": "src/stale.py",
                    "source_location": "L99",
                }
            ],
        )
        offenses = checker.check_graph(tmp_path, graph)
        assert len(offenses) == 1
        assert "beyond" in offenses[0].reason

    def test_a_renamed_symbol_is_stale(self, tmp_path: Path) -> None:
        checker = _load_checker()
        _write_source(tmp_path, "src/renamed.py", "def new_name() -> None:\n    pass\n")
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "old_name()",
                    "file_type": "code",
                    "source_file": "src/renamed.py",
                    "source_location": "L1",
                }
            ],
        )
        offenses = checker.check_graph(tmp_path, graph)
        assert len(offenses) == 1
        assert "no longer exists" in offenses[0].reason

    def test_a_missing_source_file_is_stale(self, tmp_path: Path) -> None:
        checker = _load_checker()
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "gone()",
                    "file_type": "code",
                    "source_file": "src/gone.py",
                    "source_location": "L1",
                }
            ],
        )
        offenses = checker.check_graph(tmp_path, graph)
        assert len(offenses) == 1
        assert "no longer exists" in offenses[0].reason

    def test_rationale_nodes_are_not_location_checked(self, tmp_path: Path) -> None:
        checker = _load_checker()
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "A rationale",
                    "file_type": "rationale",
                    "source_file": "src/gone.py",
                    "source_location": "L99",
                }
            ],
        )
        assert checker.check_graph(tmp_path, graph) == []


class TestMain:
    def test_missing_graph_is_a_noop(self, tmp_path: Path, monkeypatch, capsys) -> None:
        checker = _load_checker()
        monkeypatch.setattr(checker, "ROOT", tmp_path)
        monkeypatch.setattr(checker, "DEFAULT_GRAPH", tmp_path / "missing.json")

        assert checker.main([]) == 0
        assert "SKIP" in capsys.readouterr().out

    def test_a_stale_graph_exits_nonzero(self, tmp_path: Path, monkeypatch, capsys) -> None:
        checker = _load_checker()
        _write_source(tmp_path, "src/stale.py", "def present() -> None:\n    pass\n")
        graph = _write_graph(
            tmp_path,
            [
                {
                    "label": "present()",
                    "file_type": "code",
                    "source_file": "src/stale.py",
                    "source_location": "L99",
                }
            ],
        )
        monkeypatch.setattr(checker, "ROOT", tmp_path)

        assert checker.main(["--graph", str(graph)]) == 1
        assert "stale source locations" in capsys.readouterr().out
