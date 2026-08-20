# providers/visual/

Concrete `VisualProvider` / `AudioProvider` implementations (called from a
`SceneRenderer`, never directly) will live here. No vendor selected — see
[`docs/decisions/ADR-004-media-generation-abstraction.md`](../../docs/decisions/ADR-004-media-generation-abstraction.md).

Must satisfy [`core.interfaces.visual_provider.VisualProvider`](../../core/interfaces/visual_provider.py)
or [`core.interfaces.audio_provider.AudioProvider`](../../core/interfaces/audio_provider.py).
