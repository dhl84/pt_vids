"""Self-checks for the classify stall monitor. Run: uv run python test_classify.py"""
import itertools

import requests

import main


def test_normal_stream_joins_response():
    lines = [b'{"response":"[]"}', b'{"response":"","done":true}']
    assert main._consume_stream(iter(lines), budget_s=5.0) == "[]"


def test_stalling_model_is_aborted():
    # a model that streams junk forever and never says done -> must abort, not hang
    forever = (b'{"response":"contextually-wise, "}' for _ in itertools.count())
    # fake clock: jumps past the budget on the 2nd reading
    clock = iter([0.0, 1.0, 99.0]).__next__
    try:
        main._consume_stream(forever, budget_s=10.0, now=clock)
        assert False, "expected a Timeout abort"
    except requests.exceptions.Timeout:
        pass


def test_apply_labels_external_classifier(tmp_path=None):
    import json
    import tempfile
    from pathlib import Path
    segs = [main.Segment(i, i * 1000, i * 1000 + 900, f"cue {i}") for i in (1, 2, 3)]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"index": 2, "label": "CUT", "reason": "chatter"}], f)
    n = main.apply_labels(segs, Path(f.name))
    assert n == 1
    assert [s.label for s in segs] == ["KEEP", "CUT", "KEEP"]  # unlisted stay KEEP
    assert segs[1].reason == "chatter"


if __name__ == "__main__":
    test_normal_stream_joins_response()
    test_stalling_model_is_aborted()
    test_apply_labels_external_classifier()
    print("ok")
