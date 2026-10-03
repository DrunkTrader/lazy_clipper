from backend.app.services.captions import build_ass, select_words


def test_select_words_clamps_to_clip_and_builds_highlighted_sliding_windows():
    words = select_words([
        {"words": [
            {"text": "before", "start": 0, "end": 1},
            {"text": "first", "start": 1, "end": 1.5},
            {"text": "second", "start": 1.5, "end": 2.25},
            {"text": "after", "start": 2.25, "end": 3},
        ]},
    ], 1.25, 2.5)

    assert words == [
        {"word": "first", "start": 1.25, "end": 1.5},
        {"word": "second", "start": 1.5, "end": 2.25},
        {"word": "after", "start": 2.25, "end": 2.5},
    ]
    ass = build_ass(words, 1.25, 2.5)
    assert "PlayResX: 1080" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:00.25" in ass
    assert "{\\c&H0000BFFF&}first" in ass
    assert "{\\c&H0000BFFF&}second" in ass
