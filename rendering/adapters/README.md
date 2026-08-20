# rendering/adapters/

`MoviePyVideoRenderer` (`moviepy_video_renderer.py`) — the concrete
`VideoRenderer` chosen in practice for the hackathon build (ADR-005 itself
remains formally deferred; nothing benchmarked the alternatives). Built on
MoviePy v2, backed by the imageio-ffmpeg binary MoviePy installs
automatically, so no system `ffmpeg` install is required.

Implements the Assembly Sequence from `docs/workflow.md`'s Video
Composition stage as a "news package" composite: ONE continuous Ken
Burns B-roll background (each image given a subtle zoom, with a lead-in
segment reusing the first image for the avatar's own duration) runs the
whole video, with the Phase 3 avatar hook clip (audio already baked in)
overlaid on top as a large, bottom-anchored picture-in-picture for
exactly its own duration — "reporter over B-roll", not a hard cut
between an avatar scene and a B-roll scene. The Phase 2 `body_audio.wav`
plays under the B-roll portion, and an optional burned-in caption bar
covers the B-roll after the avatar overlay ends.

`compose_final_video()` is the primary entry point (explicit file paths
in, one `VideoAsset` out); `render()` implements the generic
`VideoRenderer` ABC by deriving those paths from a `Scene`/`AudioAsset`/
`MediaAsset` list — see the class docstring for the exact derivation rule.

Satisfies [`rendering.interfaces.VideoRenderer`](../interfaces/video_renderer.py).
Nothing in `core/`, `agents/`, or `backend/` should ever import directly
from this directory — only through `rendering.interfaces.VideoRenderer`.
