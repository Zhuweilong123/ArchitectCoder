from radar_sim.debug_trace import DebugTrace


def test_debug_trace_records_event():
    assert DebugTrace().record("ok") == "trace:ok"
