# providers/video/

Concrete `VideoGenerationProvider` implementations (an AI video/avatar
vendor, called from a `SceneRenderer`) will live here. No vendor
selected — see
[`docs/decisions/ADR-004-media-generation-abstraction.md`](../../docs/decisions/ADR-004-media-generation-abstraction.md).

Must satisfy [`core.interfaces.video_provider.VideoGenerationProvider`](../../core/interfaces/video_provider.py).
