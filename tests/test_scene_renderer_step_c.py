"""Step C smoke test (Phase 2 media plan):
1. Render one TEXT scene with HtmlSceneRenderer + Playwright -> real PNG.
2. Verify PNG dimensions and that it's actually openable.
3. Explicitly test the Pillow fallback by pointing HtmlSceneRenderer at a
   nonexistent Chromium binary (a real launch failure, not a mock) and
   confirming SceneRendererRegistry.render_with_fallback still produces
   a PNG via PilSceneRenderer.
4. Confirm both renderers satisfy the SceneRenderer ABC.
5. Confirm the fallback is registered in the right priority and doesn't
   interfere with the primary renderer's normal (non-failing) path.

Run directly: python tests/test_scene_renderer_step_c.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from core.interfaces.scene_renderer import SceneRenderer, SceneRendererRegistry  # noqa: E402
from core.models.enums import GenerationStatus, NarrativeRole, SceneType  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from rendering.adapters.html_scene_renderer import HtmlSceneRenderer, VIEWPORT_WIDTH, VIEWPORT_HEIGHT  # noqa: E402
from rendering.adapters.pil_scene_renderer import PilSceneRenderer  # noqa: E402


def make_scene() -> Scene:
    return Scene(
        storyboard_id="smoke-test-step-c",
        order_index=0,
        scene_type=SceneType.TEXT,
        narrative_role=NarrativeRole.BENEFIT,
        narration_segment_text="This scheme provides a subsidy of five thousand rupees.",
        duration_seconds=4.25,  # matches Step B's synthesized English duration
    )


def check_png(path: str, expected_size: tuple[int, int] | None = None) -> tuple[int, int]:
    """Opens the PNG for real (not just checking the file exists) to
    confirm it's genuinely readable image data, then returns its size."""
    with Image.open(path) as img:
        img.verify()  # raises if the file isn't a valid image
    with Image.open(path) as img:  # re-open: verify() leaves the file unusable for further ops
        size = img.size
        fmt = img.format
    assert fmt == "PNG", f"expected PNG, got {fmt}"
    if expected_size is not None:
        assert size == expected_size, f"expected {expected_size}, got {size}"
    return size


def main() -> None:
    scene = make_scene()

    # ---- 1 & 2: HtmlSceneRenderer + Playwright -> real PNG, verify dimensions/openable
    print("=== 1-2. HtmlSceneRenderer (Playwright) ===")
    html_renderer = HtmlSceneRenderer()
    html_asset = html_renderer.render_scene(scene)
    assert html_asset.generation_status == GenerationStatus.COMPLETE
    assert html_asset.provider_name == "html-playwright"
    html_size = check_png(html_asset.storage_path, expected_size=(VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
    print(f"  storage_path: {html_asset.storage_path}")
    print(f"  dimensions:   {html_size[0]}x{html_size[1]}")
    print(f"  file size:    {Path(html_asset.storage_path).stat().st_size} bytes")
    print("  PASS: real, openable PNG at the expected viewport size")

    # ---- 4: HtmlSceneRenderer with Chromium "disabled" (bogus executable path)
    #         -> real launch failure, not a mock
    print("\n=== 3-4. Pillow fallback, triggered by a real Playwright launch failure ===")
    broken_html_renderer = HtmlSceneRenderer(chromium_executable_path="/nonexistent/chromium-binary")
    try:
        broken_html_renderer.render_scene(scene)
        raise AssertionError("expected HtmlSceneRenderer to raise with a bogus executable_path")
    except AssertionError:
        raise
    except Exception as exc:
        print(f"  confirmed: HtmlSceneRenderer raises as expected when Chromium is unavailable ({type(exc).__name__})")

    # ---- 5: PilSceneRenderer standalone -> a second real PNG
    pil_renderer = PilSceneRenderer()
    pil_asset = pil_renderer.render_scene(scene)
    assert pil_asset.generation_status == GenerationStatus.COMPLETE
    assert pil_asset.provider_name == "pillow-fallback"
    pil_size = check_png(pil_asset.storage_path, expected_size=(VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
    print(f"  storage_path: {pil_asset.storage_path}")
    print(f"  dimensions:   {pil_size[0]}x{pil_size[1]}")
    print(f"  file size:    {Path(pil_asset.storage_path).stat().st_size} bytes")
    print("  PASS: PilSceneRenderer alone produces a real, openable PNG")

    # ---- 6: both satisfy the SceneRenderer ABC
    print("\n=== 6. Interface conformance ===")
    assert isinstance(html_renderer, SceneRenderer)
    assert isinstance(pil_renderer, SceneRenderer)
    assert html_renderer.supports(SceneType.TEXT) is True
    assert pil_renderer.supports(SceneType.TEXT) is True
    print("  PASS: HtmlSceneRenderer and PilSceneRenderer are both valid SceneRenderer implementations")

    # ---- 7a: registered together, HtmlSceneRenderer (working) stays primary — no interference
    print("\n=== 7. Registry: priority + fallback behavior ===")
    registry = SceneRendererRegistry()
    registry.register(html_renderer)  # primary, registered first
    registry.register(pil_renderer)  # fallback, registered second (lower priority)

    resolved = registry.get_renderer(SceneType.TEXT)
    assert resolved is html_renderer, "registry did not prioritize the primary (HtmlSceneRenderer) renderer"
    print("  get_renderer(TEXT) -> HtmlSceneRenderer (primary), as expected")

    fallback_result_asset = registry.render_with_fallback(scene)
    assert fallback_result_asset.provider_name == "html-playwright"
    print("  render_with_fallback(scene) with a healthy primary -> served by HtmlSceneRenderer (no interference)")

    # ---- 7b: same registry, but primary is broken -> registry itself falls through to Pillow
    broken_registry = SceneRendererRegistry()
    broken_registry.register(broken_html_renderer)  # primary, but Chromium is unavailable
    broken_registry.register(pil_renderer)  # fallback

    fallback_asset = broken_registry.render_with_fallback(scene)
    assert fallback_asset.generation_status == GenerationStatus.COMPLETE
    assert fallback_asset.provider_name == "pillow-fallback"
    check_png(fallback_asset.storage_path, expected_size=(VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
    print("  render_with_fallback(scene) with a broken primary -> registry itself fell through to PilSceneRenderer")
    print(f"    -> {fallback_asset.storage_path}")

    print("\n=== Step C result: PASS — all 7 checks satisfied ===")


if __name__ == "__main__":
    main()
