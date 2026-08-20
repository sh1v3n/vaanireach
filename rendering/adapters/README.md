# rendering/adapters/

`MoviePyVideoRenderer` (`moviepy_video_renderer.py`) — the concrete
`VideoRenderer` chosen in practice for the hackathon build (ADR-005 itself
remains formally deferred; nothing benchmarked the alternatives). Built on
MoviePy v2, backed by the imageio-ffmpeg binary MoviePy installs
automatically, so no system `ffmpeg` install is required.

Implements the Assembly Sequence from `docs/workflow.md`'s Video
Composition stage: the Phase 3 avatar hook clip (audio already baked in)
as Scene 1, followed by the Phase 4 Gemini/Imagen B-roll images — each
given a subtle Ken Burns zoom and a duration of
`body_audio.duration / image_count` — with the Phase 2 `body_audio.wav`
overlaid, and an optional burned-in caption bar over the B-roll.

`compose_final_video()` is the primary entry point (explicit file paths
in, one `VideoAsset` out); `render()` implements the generic
`VideoRenderer` ABC by deriving those paths from a `Scene`/`AudioAsset`/
`MediaAsset` list — see the class docstring for the exact derivation rule.

Satisfies [`rendering.interfaces.VideoRenderer`](../interfaces/video_renderer.py).
Nothing in `core/`, `agents/`, or `backend/` should ever import directly
from this directory — only through `rendering.interfaces.VideoRenderer`.
