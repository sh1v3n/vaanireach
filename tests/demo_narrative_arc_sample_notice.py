"""Demonstration script (adjustment #9/#11): prints the full NarrativeArc
and every Scene TemplateStoryDirector produces for the sample notice —
not just pass/fail. Not a test; a review artifact. Run directly:
    python tests/demo_narrative_arc_sample_notice.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402


def main() -> None:
    facts = sample_notice_facts()
    facts_by_id = {f.id: f for f in facts}

    director = TemplateStoryDirector()
    arc, scenes = director.plan_narrative_arc(facts)

    print("=" * 78)
    print("NarrativeArc")
    print("=" * 78)
    print(f"  id:                      {arc.id}")
    print(f"  title:                   {arc.title}")
    print(f"  story_summary:           {arc.story_summary}")
    print(f"  target_duration_seconds: {arc.target_duration_seconds:.2f}s")
    print(f"  generator_name:          {arc.generator_name}")
    print(f"  status:                  {arc.status.value}")
    print(f"  scene count:             {len(scenes)}")
    print()

    for scene in scenes:
        cited = [facts_by_id[fid] for fid in scene.source_fact_ids]
        print("-" * 78)
        print(f"Scene {scene.order_index}  [{scene.narrative_role.value.upper()}]  scene_type={scene.scene_type.value}")
        print(f"  narration:  {scene.narration_segment_text}")
        print(f"  duration:   {scene.duration_seconds:.2f}s (estimated pre-TTS)")
        print(f"  cited facts ({len(cited)}):")
        for f in cited:
            print(f"    - [{f.fact_type.value}] {f.value!r}  (id={f.id})")
        print(f"  visual_concept.summary:     {scene.visual_concept.summary}")
        print(f"  visual_concept.elements:    {scene.visual_concept.elements}")
        print(f"  visual_concept.visual_beats:")
        for i, beat in enumerate(scene.visual_concept.visual_beats, start=1):
            print(f"      {i}. {beat}")
        print(f"  transition_to_next_scene: {scene.transition_to_next_scene.value if scene.transition_to_next_scene else '(none — last scene)'}")

    print("-" * 78)
    total = sum(s.duration_seconds for s in scenes)
    print(f"\nTotal estimated duration: {total:.2f}s  (target range: 30-45s)")
    print(f"Roles used: {' -> '.join(s.narrative_role.value for s in scenes)}")


if __name__ == "__main__":
    main()
