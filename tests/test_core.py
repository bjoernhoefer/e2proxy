"""Unit tests for core helpers + regressions documented in the changelog."""
from e2proxy import receivers
from e2proxy.openwebif import _owif_fmt_length
from e2proxy.probe import PROBE_UA, is_probe_request
from e2proxy.switching import wilson_interval


def test_owif_length_is_minutes_seconds():
    """v4.3: Dream Player expects MINUTES:SS, not raw seconds or H:MM:SS."""
    assert _owif_fmt_length(5400) == "90:00"
    assert _owif_fmt_length(61) == "1:01"
    assert _owif_fmt_length(0) == "" and _owif_fmt_length(None) == "" and _owif_fmt_length("x") == ""


def test_wilson_interval_bounds():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and 0 < hi < 0.35          # zero fails ≠ certainty
    lo, hi = wilson_interval(5, 10)
    assert lo < 0.5 < hi


def test_probe_requests_are_recognised():
    """v4.9: probe traffic must not pollute usage stats / access log."""
    assert is_probe_request({"User-Agent": PROBE_UA + "/1"})
    assert not is_probe_request({"User-Agent": "Plex"})
    assert not is_probe_request(None)


def test_shared_tuner_released_only_by_last_user():
    """v4.6: the first ending stream must not free a tuner another viewer uses."""
    receivers._init_receiver_state()
    epoch = receivers.acquire_receiver("rx1", "10.0.0.1", service_ref="1:0:1:A:")
    assert receivers.attach_receiver("rx1", "1:0:1:A:") == epoch
    assert receivers.attach_receiver("rx1", "1:0:1:B:") is None   # other channel
    receivers.release_receiver("rx1", epoch)
    assert receivers._receiver_state["rx1"] is not None             # still watched
    receivers.release_receiver("rx1", epoch)
    assert receivers._receiver_state["rx1"] is None


def test_stale_release_does_not_kill_new_session():
    """A late cleanup thread (old epoch) must not release a fresh session."""
    receivers._init_receiver_state()
    old = receivers.acquire_receiver("rx1", "10.0.0.1")
    receivers.release_receiver("rx1", force=True)                    # /kill
    new = receivers.acquire_receiver("rx1", "10.0.0.2")
    receivers.release_receiver("rx1", old)
    assert receivers._receiver_state["rx1"]["epoch"] == new
    receivers.release_receiver("rx1", new)
