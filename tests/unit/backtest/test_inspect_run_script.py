"""Unit tests for scripts/inspect_run.py — Phase 2 single-run drilldown CLI."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from app.modules.backtest.result_store import save_run
from scripts import inspect_run as inspect_run_script
from tests.unit.backtest.conftest import (
    _make_backtest_run,
    _make_backtest_trade,
    _make_stock_signal_record,
)


class TestInspectRunDefault:
    def test_missing_run_exits_1(self, tmp_path: Path, capsys) -> None:
        exit_code = inspect_run_script.main(
            ["does_not_exist", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "does_not_exist" in captured.err

    def test_default_output_has_header_metrics_signal_summary(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="inspected_run",
            signals=[
                _make_stock_signal_record(
                    symbol="AMZN", analyst_type="fundamentals",
                    bullish_score=7, confidence=5,
                ),
                _make_stock_signal_record(
                    symbol="AMZN", analyst_type="technical",
                    bullish_score=6, confidence=6,
                ),
                _make_stock_signal_record(
                    symbol="GOOGL", analyst_type="fundamentals",
                    bullish_score=8, confidence=6,
                ),
            ],
            llm_cache_hits=2,
            llm_cache_misses=1,
        )
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["inspected_run", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "inspected_run" in out
        assert "Config:" in out
        assert "Metrics:" in out
        assert "Signals:" in out
        assert "fundamentals: 2" in out or "fundamentals:  2" in out
        assert "technical:" in out
        assert "LLM cache" in out.lower() or "cache:" in out.lower()
        assert "2" in out and "1" in out  # cache hits/misses

    def test_default_output_does_not_dump_full_signals(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="nosignals",
            signals=[
                _make_stock_signal_record(
                    symbol="AMZN", summary="very long verbatim analyst summary here"
                )
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            ["nosignals", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "very long verbatim analyst summary here" not in captured.out

    def test_default_output_shows_trade_summary_line_not_full_list(
        self, tmp_path: Path, capsys
    ) -> None:
        trades = [
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="AAA", side="buy"),
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="BBB", side="buy"),
            _make_backtest_trade(trade_date=date(2025, 6, 9), symbol="CCC", side="sell"),
        ]
        run = _make_backtest_run(run_id="tradesrun", trades=trades)
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["tradesrun", "--runs-dir", str(tmp_path)]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Trades:" in out
        assert "3 total" in out or "3" in out.split("Trades:")[1].split("\n")[0]
        assert "buy: 2" in out
        assert "sell: 1" in out
        # Full trade rows should NOT appear.
        assert "2025-06-02 buy" not in out


class TestInspectRunDetailFlags:
    def test_trades_flag_dumps_full_list(self, tmp_path: Path, capsys) -> None:
        trades = [
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="AAA", side="buy"),
            _make_backtest_trade(trade_date=date(2025, 6, 2), symbol="BBB", side="buy"),
        ]
        run = _make_backtest_run(run_id="trades_full", trades=trades)
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["trades_full", "--runs-dir", str(tmp_path), "--trades"]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "AAA" in out
        assert "BBB" in out
        assert "2025-06-02" in out

    def test_signals_flag_dumps_full_verbatim_summaries(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="sig_full",
            signals=[
                _make_stock_signal_record(
                    symbol="AMZN",
                    summary="EXACT VERBATIM SUMMARY TEXT MARKER",
                )
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            ["sig_full", "--runs-dir", str(tmp_path), "--signals"]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "EXACT VERBATIM SUMMARY TEXT MARKER" in out

    def test_signals_top_n_limits_output(self, tmp_path: Path, capsys) -> None:
        signals = [
            _make_stock_signal_record(
                symbol=f"S{i}", analyst_type="technical",
                summary=f"summary for S{i}",
            )
            for i in range(10)
        ]
        run = _make_backtest_run(run_id="sig_topn", signals=signals)
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            [
                "sig_topn",
                "--runs-dir",
                str(tmp_path),
                "--signals",
                "--signals-top",
                "3",
            ]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        # Only 3 of 10 should appear in the rendered summaries.
        appearing = sum(1 for i in range(10) if f"summary for S{i}" in out)
        assert appearing == 3

    def test_symbol_filter_on_signals(self, tmp_path: Path, capsys) -> None:
        run = _make_backtest_run(
            run_id="sym_filter",
            signals=[
                _make_stock_signal_record(symbol="AMZN", summary="amzn_marker"),
                _make_stock_signal_record(symbol="GOOGL", summary="googl_marker"),
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            [
                "sym_filter",
                "--runs-dir",
                str(tmp_path),
                "--signals",
                "--symbol",
                "GOOGL",
            ]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "googl_marker" in out
        assert "amzn_marker" not in out

    def test_analyst_type_filter_on_signals(self, tmp_path: Path, capsys) -> None:
        run = _make_backtest_run(
            run_id="at_filter",
            signals=[
                _make_stock_signal_record(
                    analyst_type="fundamentals", summary="fund_marker"
                ),
                _make_stock_signal_record(
                    analyst_type="technical", summary="tech_marker"
                ),
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            [
                "at_filter",
                "--runs-dir",
                str(tmp_path),
                "--signals",
                "--analyst-type",
                "fundamentals",
            ]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "fund_marker" in out
        assert "tech_marker" not in out

    def test_json_flag_dumps_full_backtest_run(self, tmp_path: Path, capsys) -> None:
        run = _make_backtest_run(
            run_id="json_full",
            signals=[_make_stock_signal_record(symbol="AMZN", summary="json_sig")],
        )
        save_run(run, runs_dir=tmp_path)

        exit_code = inspect_run_script.main(
            ["json_full", "--runs-dir", str(tmp_path), "--json"]
        )
        assert exit_code == 0
        out = capsys.readouterr().out
        import json as json_lib

        parsed = json_lib.loads(out)
        assert parsed["run_id"] == "json_full"
        # Full BacktestRun dump includes signals, metrics, snapshots.
        assert parsed["signals"][0]["summary"] == "json_sig"

    def test_json_flag_with_symbol_filter_filters_signals_array(
        self, tmp_path: Path, capsys
    ) -> None:
        run = _make_backtest_run(
            run_id="json_sym",
            signals=[
                _make_stock_signal_record(symbol="AMZN", summary="amzn_json"),
                _make_stock_signal_record(symbol="GOOGL", summary="googl_json"),
            ],
        )
        save_run(run, runs_dir=tmp_path)
        exit_code = inspect_run_script.main(
            [
                "json_sym",
                "--runs-dir",
                str(tmp_path),
                "--json",
                "--symbol",
                "GOOGL",
            ]
        )
        assert exit_code == 0
        import json as json_lib

        parsed = json_lib.loads(capsys.readouterr().out)
        symbols = {s["symbol"] for s in parsed["signals"]}
        assert symbols == {"GOOGL"}
