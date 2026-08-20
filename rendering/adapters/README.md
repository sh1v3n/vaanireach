# rendering/adapters/

Empty placeholder. A concrete `VideoRenderer` implementation
(FFmpeg-based, Remotion, MoviePy, or something else — see
[`docs/decisions/ADR-005-video-rendering.md`](../../docs/decisions/ADR-005-video-rendering.md))
lands here once that decision is made. Nothing in `core/`, `agents/`, or
`backend/` should ever import directly from this directory — only through
`rendering.interfaces.VideoRenderer`.
