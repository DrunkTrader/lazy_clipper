import pytest

from backend.app.services.youtube import InvalidYouTubeURL, validate_youtube_url


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=abc123_XYzz", "abc123_XYzz"),
        ("https://youtu.be/abc123_XYzz?t=20", "abc123_XYzz"),
        ("https://www.youtube.com/shorts/abc123_XYzz", "abc123_XYzz"),
        ("https://m.youtube.com/embed/abc123_XYzz", "abc123_XYzz"),
    ],
)
def test_validate_youtube_url(url, video_id):
    assert validate_youtube_url(url) == video_id


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://example.com/watch?v=abc123_XYzz",
        "youtube.com/watch?v=abc123_XYzz",
        "https://youtube.com/watch",
        "https://user:password@youtube.com/watch?v=abc123_XYzz",
        "https://youtube.com:443/watch?v=abc123_XYzz",
        "https://youtu.be/abc123_XYzz/extra",
        "https://youtu.be/short",
        "https://www.youtube.com/watch?v=abc123_XYzz#fragment",
    ],
)
def test_validate_youtube_url_rejects_invalid_urls(url):
    with pytest.raises(InvalidYouTubeURL):
        validate_youtube_url(url)
