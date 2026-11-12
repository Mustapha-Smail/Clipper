from clipper.moments import Pick, build_lines, picks_to_moments


def words(*items):
    return [{"w": w, "s": s, "e": e} for w, s, e in items]


def make_line(i, start, end):
    return {"i": i, "start": start, "end": end, "text": f"line {i}"}


def pick(first, last, score=5, title="t"):
    return Pick(first_line=first, last_line=last, hook="h", title=title, caption="c", reason="r", score=score)


def test_build_lines_splits_on_sentence_end_and_long_pauses():
    lines = build_lines(words(("Hello", 0, 0.4), ("there.", 0.5, 0.9), ("Next", 1.0, 1.3),
                              ("bit", 1.4, 1.6), ("after", 4.0, 4.3), ("pause.", 4.4, 4.8)))
    assert [line["text"] for line in lines] == ["Hello there.", "Next bit", "after pause."]
    assert lines[0]["start"] == 0 and lines[0]["end"] == 0.9
    assert [line["i"] for line in lines] == [0, 1, 2]


LINES = [make_line(i, i * 10, i * 10 + 9) for i in range(30)]


def test_picks_become_padded_moments_ordered_by_score():
    found, warnings = picks_to_moments([pick(0, 2, score=6, title="low"), pick(10, 12, score=9, title="high")],
                                       LINES, 300, n=5, min_len=20, max_len=60)
    assert [m["title"] for m in found] == ["high", "low"]
    assert found[1]["start"] == 0.0                      # padding never goes below zero
    assert found[0]["start"] == 99.85 and found[0]["end"] == 129.3
    assert warnings == []


def test_out_of_range_length_and_overlap_are_skipped_with_a_warning():
    found, warnings = picks_to_moments(
        [pick(0, 2, score=9, title="keep"), pick(1, 3, score=8, title="overlap"),
         pick(5, 5, score=7, title="short"), pick(40, 41, score=6, title="bogus")],
        LINES, 300, n=5, min_len=20, max_len=60)
    assert [m["title"] for m in found] == ["keep"]
    assert len(warnings) == 3
    assert any("overlaps" in w for w in warnings) and any("length" in w for w in warnings)


def test_ranges_already_taken_block_new_picks():
    found, _ = picks_to_moments([pick(0, 2, title="clash"), pick(10, 12, title="free")],
                                LINES, 300, n=5, min_len=20, max_len=60, taken=[(5.0, 25.0)])
    assert [m["title"] for m in found] == ["free"]


def test_no_more_than_n_moments():
    picks = [pick(i * 4, i * 4 + 2, score=10 - i, title=str(i)) for i in range(6)]
    found, _ = picks_to_moments(picks, LINES, 300, n=3, min_len=20, max_len=60)
    assert [m["title"] for m in found] == ["0", "1", "2"]
