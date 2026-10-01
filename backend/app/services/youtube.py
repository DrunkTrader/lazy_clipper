"""YouTube URL validation and yt-dlp integration."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import re


class MediaDependencyError(RuntimeError):
    pass


class InvalidYouTubeURL(ValueError):
    pass


_ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_DOWNLOAD_TIMEOUT = 60
_MAX_DOWNLOAD_BYTES = 750 * 1024 * 1024


def _canonical_video_url(video_id: str) -> str:
    return "https://www.youtube.com/watch?" + urlencode({"v": video_id})


def validate_youtube_url(url: str) -> str:
    """Validate a public YouTube URL and return its strict video id.

    This deliberately rejects credentials and ports in the netloc.  The caller
    must use :func:`canonical_youtube_url` for network access, rather than
    passing a user-controlled URL to yt-dlp.
    """
    if not isinstance(url, str) or not url or any(c.isspace() for c in url):
        raise InvalidYouTubeURL("Invalid URL")
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise InvalidYouTubeURL("Invalid URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or host not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or ":" in parsed.netloc
        or parsed.fragment
    ):
        raise InvalidYouTubeURL("URL must be an http(s) YouTube URL without credentials or a port")

    video_id = ""
    if host.endswith("youtu.be"):
        video_id = parsed.path[1:] if parsed.path.startswith("/") and parsed.path.count("/") == 1 else ""
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query, keep_blank_values=True).get("v", [""])[0]
    elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
        parts = parsed.path.split("/")
        video_id = parts[2] if len(parts) == 3 else ""
    if not _VIDEO_ID.fullmatch(video_id):
        raise InvalidYouTubeURL("URL does not contain a valid 11-character YouTube video id")
    return video_id


def canonical_youtube_url(url: str) -> str:
    """Return the only URL form this service sends to yt-dlp."""
    return _canonical_video_url(validate_youtube_url(url))


@dataclass(frozen=True)
class VideoMetadata:
    youtube_id: str
    title: str
    duration: float | None
    thumbnail_url: str | None


class YoutubeService:
    def _module(self):
        try:
            import yt_dlp  # lazy optional dependency
        except ImportError as exc:
            raise MediaDependencyError("yt-dlp is required for YouTube ingestion") from exc
        return yt_dlp

    @staticmethod
    def _options(*, skip_download: bool) -> dict:
        return {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": skip_download,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 2,
            "socket_timeout": _DOWNLOAD_TIMEOUT,
            "match_filter": YoutubeService._live_match_filter,
        }

    @staticmethod
    def _reject_live(info: dict) -> None:
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise MediaDependencyError("Live streams are not supported")

    @staticmethod
    def _live_match_filter(info: dict, *, incomplete: bool = False) -> str | None:
        del incomplete
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            return "Live streams are not supported"
        return None

    def get_metadata(self, url: str) -> VideoMetadata:
        video_id = validate_youtube_url(url)
        yt_dlp = self._module()
        options = self._options(skip_download=True)
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(_canonical_video_url(video_id), download=False)
            self._reject_live(info)
            actual_id = str(info.get("id") or video_id)
            if actual_id != video_id:
                raise MediaDependencyError("YouTube returned a different video id")
        except MediaDependencyError:
            raise
        except Exception as exc:
            raise MediaDependencyError(f"Could not read YouTube metadata: {exc}") from exc
        return VideoMetadata(
            youtube_id=actual_id,
            title=str(info.get("title") or "Untitled video"),
            duration=float(info["duration"]) if info.get("duration") is not None else None,
            thumbnail_url=info.get("thumbnail"),
        )

    def download_video(self, url: str, destination: Path) -> Path:
        video_id = validate_youtube_url(url)
        yt_dlp = self._module()
        destination.mkdir(parents=True, exist_ok=True)
        output_template = str(destination / "video.%(ext)s")
        # Never mistake an interrupted previous run for a successful download.
        for stale in destination.glob("video.*"):
            if stale.is_file():
                stale.unlink()
        options = self._options(skip_download=False)
        options.update({
            # Prefer a browser-compatible H.264/AAC MP4 and cap the selected size.
            "format": "bv*[vcodec^=avc1][ext=mp4][height<=1080]+ba[ext=m4a]/b[vcodec^=avc1][ext=mp4][height<=1080]/bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]",
            "merge_output_format": "mp4",
            "outtmpl": output_template,
            "max_filesize": _MAX_DOWNLOAD_BYTES,
            "overwrites": True,
        })
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(_canonical_video_url(video_id), download=True)
                self._reject_live(info)
                actual_id = str(info.get("id") or video_id)
                if actual_id != video_id:
                    raise MediaDependencyError("YouTube returned a different video id")
                prepared = Path(downloader.prepare_filename(info))
        except MediaDependencyError:
            for stale in destination.glob("video.*"):
                if stale.is_file():
                    stale.unlink()
            raise
        except Exception as exc:
            for stale in destination.glob("video.*"):
                if stale.is_file():
                    stale.unlink()
            raise MediaDependencyError(f"Could not download YouTube video: {exc}") from exc
        merged = destination / "video.mp4"
        if merged.is_file() and merged.stat().st_size > 0:
            return merged
        # Do not return a webm/partial file: callers require browser-compatible MP4.
        if prepared.exists():
            prepared.unlink()
        raise MediaDependencyError("yt-dlp reported success but no complete MP4 video was found")
