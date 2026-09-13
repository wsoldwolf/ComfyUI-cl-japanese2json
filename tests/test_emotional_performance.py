"""Regressions for mouth, eyebrow and synthetic-motion failures in plan 00006."""

import json
import unittest
from dataclasses import replace

from .helpers import module
from .test_mv_prompt_planner import (
    BRIEF, TIMELINE, FakePlannerBackend, brief_parser, planning, structures,
    timeline_parser, validation, lyric_blueprint, parse_lyric_blueprint,
    scene_payload, song_bible,
)

policy = module("node_mv_prompt_planner.performance_policy")
repair = module("node_mv_prompt_planner.motion_repair")


class EmotionalPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.timeline = timeline_parser.parse_prompt_timeline(TIMELINE).scenes[1]
        self.protector = module("node_mv_prompt_planner.placeholders").ReferenceProtector()
        self.protector.protect(BRIEF)
        self.contact = "<Subject 1>は右手を対象面に押し当てる。"
        self.release = "<Subject 1>は右手を対象面から離す。"
        self.result = "対象面に窪みが残る。"
        self.blueprint = structures.LyricActionBlueprint(
            2, 1, "direct_subject_action", "対象面を捉える。",
            (self.contact, self.release), self.result,
        )
        self.scene = structures.PlannedScene(2, "押す。", (
            structures.PlannedShot(0, "<Subject 1>と対象面を捉える。",
                (self.contact, self.release, self.result), "月光。",
                structures.CameraPlan("static", "none", "none", "固定する。")),
        ), 1, "direct_subject_action")

    def response(self, actions):
        return "\n".join(("MOTION_REPAIR\t2", "SHOT\t0", *(
            f"ACTION\t{i}\t{self.protector.protect(a)}" for i, a in enumerate(actions, 1)
        ), "END_SHOT", "END_MOTION_REPAIR"))

    def test_mouth_and_eyebrow_actions_rejected_before_lyric_lock(self):
        for value in (
            "CLMPSUB1Xの舌を上顎に押し当てて、唇を左右に広げる。",
            "CLMPSUB1Xの唇を下向きに引き下げて、口を開く。",
            "CLMPSUB1Xの唇の表面に金色の光が広がる。",
            "CLMPSUB1Xの右眉を上へ引き上げる。",
            "CLMPSUB1Xの左眉を下へ下げて揺らす。",
        ):
            with self.subTest(value=value):
                raw = lyric_blueprint().replace(
                    "CLMPSUB1Xは対象面へ踏み込みながら片腕を引く。", value)
                recovered, errors = parse_lyric_blueprint(raw)
                self.assertFalse(recovered)
                self.assertRegex(errors[2], "forbidden (mouth|eyebrow) choreography")
                self.assertEqual(planning._failed_lyric_anchor(raw, scene_id=2, error=errors[2]), 1)

    def test_facial_design_and_external_eating_action_are_not_rejected(self):
        for value in (
            "<Subject 1>は豆粒形の眉を維持し、右腕を前へ伸ばす。",
            "<Subject 1>は果物を口に運ぶために右手を上げる。",
            "<Subject 1>の顔と口元を斜めから捉える。",
            "<Subject 1>の口元を月光で照らす。",
        ):
            self.assertIsNone(validation.performance_safety_error(value))

    def test_safety_checks_composition_and_auxiliary_too(self):
        for field in ("composition", "environment"):
            shot = replace(self.scene.shots[0], **{field: "狐の耳と眉が葉の音に応じて揺れている。"})
            self.assertTrue(validation.performance_safety_issues(replace(self.scene, shots=(shot,))))

    def test_continuity_never_carries_eyebrow_action_or_camera_prose(self):
        shot = replace(self.scene.shots[0], composition="眉が揺れる。",
            subject_actions=("<Subject 1>の眉が揺れる。",),
            environment="眉が揺れる。",
            camera=structures.CameraPlan("arc", "large", "fast", "眉が揺れる。"))
        tail = planning._previous_scene_tail(replace(self.scene, shots=(shot,)), self.protector)
        self.assertNotIn("眉", json.dumps(tail, ensure_ascii=False))
        self.assertEqual(tail["subject_references"], ["CLMPSUB1X"])
        self.assertEqual(tail["camera"]["type"], "arc")

    def test_wording_duplicates_do_not_count_as_new_action(self):
        actions = ("<Subject 1>の右手を上げる", "<Subject 1>は右手をゆっくり上げる",
                   "<Subject 1>の左手を上げる", "<Subject 1>の右手をもう一度上げる")
        self.assertEqual(policy.deduplicate_actions(actions), (actions[0], *actions[2:]))

    def test_last_shot_cannot_be_only_result_even_if_scene_total_passes(self):
        first = replace(self.scene.shots[0], subject_actions=(
            self.contact, self.release, "<Subject 1>は左足を踏み出す。", "<Subject 1>は胴体をひねる。"))
        last = replace(first, start_ms=7000, subject_actions=("<Subject 1>は静止した姿勢を保つ。",))
        issues = validation.subject_motion_issues(replace(self.scene, shots=(first, last)), duration_seconds=14)
        self.assertTrue(any(i.get("shot_number") == 2 for i in issues))

    def test_repair_preserves_locked_operation_result_and_non_action_fields(self):
        actions = ("<Subject 1>は左足を踏み込む。", self.contact, self.release,
                   "<Subject 1>は胴体を起こす。", self.result)
        updated = repair.parse_motion_repair(self.response(actions), self.scene, self.timeline, self.protector, self.blueprint)
        self.assertEqual(updated.shots[0].subject_actions, actions)
        self.assertEqual(replace(updated.shots[0], subject_actions=self.scene.shots[0].subject_actions), self.scene.shots[0])
        for invalid in (
            (self.release, self.contact, self.result),
            ("<Subject 1>は右手を振る。", self.release, self.result),
            (self.contact, self.release, "別の対象が壊れる。"),
        ):
            with self.assertRaises(validation.PlannerResponseError):
                repair.parse_motion_repair(self.response(invalid), self.scene, self.timeline, self.protector, self.blueprint)

    def test_merge_does_not_copy_locked_phase_to_another_shot(self):
        shot = self.scene.shots[0]
        scene = replace(self.scene, shots=(shot, replace(shot, start_ms=3000)))
        merged = planning._apply_lyric_action_blueprint(scene, self.blueprint)
        self.assertIn(self.contact, merged.shots[0].subject_actions)
        self.assertNotIn(self.release, merged.shots[0].subject_actions)
        self.assertIn(self.release, merged.shots[1].subject_actions)
        self.assertNotIn(self.contact, merged.shots[1].subject_actions)

    def test_repair_rejects_changed_timing_and_facial_manipulation(self):
        valid = self.response((self.contact, self.release, self.result))
        for raw in (valid.replace("SHOT\t0", "SHOT\t1"),
                    valid.replace(self.protector.protect(self.contact), "CLMPSUB1Xの唇を広げて口を開く。")):
            with self.assertRaises(validation.PlannerResponseError):
                repair.parse_motion_repair(raw, self.scene, self.timeline, self.protector, self.blueprint)

    def test_production_repairs_only_action_fields_without_templates(self):
        # The voiced Scene deliberately has one phase instead of its required two.
        source = scene_payload(2, actions=("CLMPSUB1Xは対象面へ右手を伸ばす。",))
        output = self.response(("<Subject 1>は対象面へ右手を伸ばす。", "<Subject 1>は指先を対象面に当てる。"))
        backend = FakePlannerBackend([song_bible(), scene_payload(1) + "\n" + source, output])
        plan = planning.generate_mv_plan(brief_parser.parse_planning_brief(BRIEF),
            timeline_parser.parse_prompt_timeline(TIMELINE), backend, retry_max=1)
        self.assertEqual(len(backend.calls), 3)
        request = json.loads(backend.calls[-1]["messages"][1]["content"])
        self.assertEqual(request["protocol"], "clmv-motion-repair-v1")
        self.assertEqual(request["shots"][0]["camera"]["type"], plan.scenes[1].shots[0].camera.type)
        self.assertEqual(plan.scenes[1].shots[0].subject_actions[-1], "<Subject 1>は指先を対象面に当てる。")
        self.assertEqual(plan.metadata["motion_repairs"][0]["method"], "targeted_llm")

    def test_targeted_attempts_are_bounded_and_invalid_output_not_adopted(self):
        backend = FakePlannerBackend(["invalid", "invalid"])
        updated, attempts, error = planning._repair_scene_actions(
            backend, scene=self.scene, timeline=self.timeline, blueprint=self.blueprint,
            planning_brief={}, protector=self.protector,
            call_settings=dict(max_tokens=1024, temperature=0.1, top_p=0.9, repetition_penalty=1.05, seed=1),
            request_index=0, maximum_attempts=2,
            progress_callback=None, interrupt_callback=None, debug_events=None,
        )
        self.assertIsNone(updated)
        self.assertEqual(attempts, 2)
        self.assertEqual(len(backend.calls), 2)
        self.assertTrue(error)
