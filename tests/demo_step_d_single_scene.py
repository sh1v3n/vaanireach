"""Step D demonstration: composes one real scene into a persistent MP4
under data/video/final/ (not pytest's tmp_path) so it can be inspected
and played after the run. Run directly:
    PYTHONPATH=backend .venv/bin/python tests/demo_step_d_single_scene.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models.enums import LanguageCode  # noqa: E402
from providers.tts.edge_tts_provider import EdgeTtsProvider  # noqa: E402
from rendering.adapters.html_scene_renderer import HtmlSceneRenderer  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_scene_srt  # noqa: E402
from tests.test_scene_renderer_step_c import make_scene  # noqa: E402

NARRATION = "This scheme provides a subsidy of five thousand rupees."


def main() -> None:
    scene = make_scene()
    print(f"Scene narration: {scene.narration_segment_text!r}")

    print("\n--- Step B: Edge TTS synthesis ---")
    audio_asset = EdgeTtsProvider().synthesize(NARRATION, LanguageCode.EN, project_id="step-d-demo")
    print(f"  audio: {audio_asset.storage_path}  ({audio_asset.duration_seconds:.3f}s)")

    print("\n--- Step C: HTML scene render ---")
    image_asset = HtmlSceneRenderer().render_scene(scene)
    print(f"  image: {image_asset.storage_path}")

    print("\n--- Step D: ffmpeg composition ---")
    video_asset = FfmpegVideoRenderer().compose_single_scene(
        image_path=image_asset.storage_path,
        audio_path=audio_asset.storage_path,
        narration_text=NARRATION,
        project_id="step-d-demo",
    )
    print(f"  video: {video_asset.storage_path_mp4}")

    srt_text = build_scene_srt(NARRATION, start_seconds=0.0, duration_seconds=audio_asset.duration_seconds)
    srt_path = Path(video_asset.storage_path_mp4).with_suffix(".srt")
    srt_path.write_text(srt_text, encoding="utf-8")
    print(f"  srt:   {srt_path}")

    print("\n--- ffprobe verification ---")
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         video_asset.storage_path_mp4],
        capture_output=True, text=True, check=True,
    ).stdout)
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    duration = float(probe["format"]["duration"])
    size_bytes = int(probe["format"]["size"])

    print(f"  duration:        {duration:.3f}s  (audio was {audio_asset.duration_seconds:.3f}s)")
    print(f"  resolution:      {video_stream['width']}x{video_stream['height']}")
    print(f"  video codec:     {video_stream['codec_name']} ({video_stream.get('profile', '?')})")
    print(f"  audio codec:     {audio_stream['codec_name']} @ {audio_stream.get('sample_rate','?')}Hz")
    print(f"  file size:       {size_bytes} bytes")

    print("\n--- decode integrity (headless playback verification) ---")
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video_asset.storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    print(f"  ffmpeg full-decode exit code: {decode.returncode}  stderr: {decode.stderr.strip() or '(none)'}")

    print("\n--- SRT contents ---")
    print(srt_text)

    print("=== DONE ===")
    print(f"MP4:  {video_asset.storage_path_mp4}")
    print(f"SRT:  {srt_path}")


if __name__ == "__main__":
    main()
