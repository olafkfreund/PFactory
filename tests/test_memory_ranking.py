"""Age-based retrieval ranking for gotchas (RFC-0021 Phase 5)."""

from datetime import UTC, datetime, timedelta

from memory import retention

NOW = datetime(2026, 11, 17, tzinfo=UTC)
LIMIT = 1000
LOW = "## Low confidence"


def _entry(days, body):
    stamp = (NOW - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return f"\n## [{stamp}]\n{body}\n"


def _body(tag, size=170):
    return (tag + " ").ljust(size, "x")


def _legacy_context(memory_dir, text, limit, now):
    """The Phase 2 function (file-position tail cut), kept to measure against."""
    entries = retention.split_gotchas(text)
    if not entries:
        return text[-limit:]
    confirmed = retention._read(memory_dir)
    demote_days, expire_days = retention.windows()
    fresh, demoted, expired = [], [], False
    for entry, body, heading_time in entries:
        age = now - retention._last_confirmed(heading_time, body, confirmed)
        if age >= timedelta(days=expire_days):
            expired = True
        elif age >= timedelta(days=demote_days):
            demoted.append(entry)
        else:
            fresh.append(entry)
    if not demoted and not expired:
        return text[-limit:]
    first = retention.HEADING.search(text)
    fresh_text = ((text[: first.start()] if first else "") + "".join(fresh))[-limit:]
    if not demoted:
        return fresh_text
    heading = f"\n## Low confidence (not re-observed in {demote_days} days)\n"
    remaining = limit - len(fresh_text) - len(heading)
    if remaining <= 0:
        return fresh_text
    return fresh_text + heading + "".join(demoted)[-remaining:]


def _stale_share(output):
    head, _, low = output.partition(LOW)
    loaded = len(retention.HEADING.findall(head))
    stale = 0
    if low:
        low = low.split("\n", 1)[1]  # drop the rest of the heading line
        stale = len(retention.HEADING.findall(low)) + (
            0 if low.startswith("## [") or not low.strip() else 1
        )
        loaded += stale
    return stale / loaded if loaded else 0.0


def _tokens(output):
    return len(output) // 4  # estimate, chars / 4


def _fixture(tmp_path):
    """Three fresh entries, three demoted ones, one expired; budget is not tight
    for the fresh set, so the leftover goes to the low-confidence block."""
    text = (
        "# Gotchas\n"
        + _entry(5, _body("fresh-a"))
        + _entry(4, _body("fresh-b"))
        + _entry(3, _body("fresh-c"))
        + _entry(40, _body("demoted-a"))
        + _entry(45, _body("demoted-b"))
        + _entry(50, _body("demoted-c"))
        + _entry(100, _body("expired"))
    )
    return text


def test_tokens_do_not_rise_and_stale_share_falls(tmp_path):
    text = _fixture(tmp_path)
    before = _legacy_context(tmp_path, text, LIMIT, NOW)
    after = retention.gotchas_context(tmp_path, text, LIMIT, NOW)
    print(
        f"ranking measure: tokens {_tokens(before)} -> {_tokens(after)}, "
        f"stale share {_stale_share(before):.2f} -> {_stale_share(after):.2f}"
    )
    assert _tokens(after) <= _tokens(before)
    assert _stale_share(after) < _stale_share(before)
    assert "expired" not in after
    assert "demoted-a" in after  # the most recently written demoted entry wins


def test_no_fragment_in_low_confidence_block(tmp_path):
    after = retention.gotchas_context(tmp_path, _fixture(tmp_path), LIMIT, NOW)
    low = after.partition(LOW)[2].split("\n", 1)[1]
    assert low.startswith("## [")


def _overflow(tmp_path):
    # A long-written entry re-confirmed yesterday sits first in the file; six
    # newer ones overflow the window, so a tail cut would drop it.
    text = "# Gotchas\n" + _entry(60, _body("reconfirmed"))
    for days in (9, 8, 7, 6, 5, 4):
        text += _entry(days, _body(f"newer-{days}"))
    retention.confirm(tmp_path, [_body("reconfirmed")], NOW - timedelta(days=1))
    return text


def test_reconfirmed_old_entry_outranks_newer_written_ones(tmp_path):
    text = _overflow(tmp_path)
    assert "reconfirmed" not in _legacy_context(tmp_path, text, LIMIT, NOW)
    after = retention.gotchas_context(tmp_path, text, LIMIT, NOW)
    assert "reconfirmed" in after
    assert len(after) <= LIMIT
    assert after.index("reconfirmed") < after.index("newer-4")  # file order


def test_oldest_confirmed_is_dropped_first(tmp_path):
    after = retention.gotchas_context(tmp_path, _overflow(tmp_path), LIMIT, NOW)
    assert "newer-4" in after and "newer-9" not in after


def test_oversized_top_entry_shows_its_tail(tmp_path):
    text = "# Gotchas\n" + _entry(1, "big " * 600)
    after = retention.gotchas_context(tmp_path, text, LIMIT, NOW)
    assert "big" in after and len(after) <= LIMIT


def test_all_fit_is_identical_to_file(tmp_path):
    text = "# Gotchas\n" + _entry(2, "one") + _entry(1, "two")
    assert retention.gotchas_context(tmp_path, text, LIMIT, NOW) == text


def test_legacy_preamble_kept_when_room_and_never_expired(tmp_path):
    text = "# Gotchas\n- legacy bullet\n" + _entry(100, "expired") + _entry(1, "new")
    after = retention.gotchas_context(tmp_path, text, LIMIT, NOW)
    assert "legacy bullet" in after and "new" in after and "expired" not in after
    tight = "# Gotchas\n- legacy bullet\n" + _entry(1, _body("new", 960))
    assert "legacy bullet" not in retention.gotchas_context(tmp_path, tight, LIMIT, NOW)


def test_no_headings_keeps_tail_slice(tmp_path):
    legacy = "# Gotchas\n" + "legacy " * 200
    assert retention.gotchas_context(tmp_path, legacy, LIMIT, NOW) == legacy[-LIMIT:]
