"""Regression cases from the repeated gestures in v2p_plan_prompt_00003."""

import unittest
from dataclasses import replace

from .helpers import module
from .test_mv_prompt_planner import (
    BRIEF, TIMELINE, FakePlannerBackend, lyric_blueprint,
    parse_lyric_blueprint, planning, brief_parser, structures,
    timeline_parser, validation,
)

policy = module("node_mv_prompt_planner.performance_policy")
camera_policy = module("node_mv_prompt_planner.camera_policy")


class PlannerMotionIntegrationTests(unittest.TestCase):
    def test_sentence_duplicates_removed_but_direction_and_repeat_preserved(self):
        lift = "<Subject 1>は右腕を上げる。"
        turn = "<Subject 1>は胴体をひねる。"
        variants = (
            "<Subject 1>は左腕を上げる。",
            "<Subject 1>は右腕を下げる。",
            "<Subject 1>は右腕を上げない。",
            "<Subject 1>はもう一度右腕を上げる。",
        )
        result = policy.deduplicate_actions((lift + "続いて" + turn, lift[:-1], turn, *variants))
        self.assertEqual(result, (lift + turn, *variants))

    def test_preplan_does_not_send_synthetic_motion_to_scene_model(self):
        timeline = timeline_parser.parse_prompt_timeline(TIMELINE)
        scene = replace(timeline.scenes[1], duration_seconds=14)
        brief = brief_parser.parse_planning_brief(BRIEF)
        protector, request_brief, _ = planning._build_protector(brief, timeline)
        backend = FakePlannerBackend([lyric_blueprint()])
        recovered, _, retries = planning._plan_lyric_action_blueprints(
            backend, scenes=[scene], planning_brief=request_brief,
            protector=protector,
            call_settings=dict(max_tokens=2048, temperature=0.1, top_p=0.9,
                               repetition_penalty=1.05, seed=1),
            retry_max=1, request_index=0, batch_number=1,
            progress_callback=None, interrupt_callback=None,
            debug_events=None, debug_state=None, scenes_per_request=1,
        )
        self.assertEqual(retries, 0)
        self.assertEqual(len(recovered[2].subject_actions), 2)
        self.assertFalse(any("別の構え" in value for value in recovered[2].subject_actions))

    def test_locked_phases_keep_preparation_and_release_order(self):
        prepare = "<Subject 1>は左足で踏み込む。"
        contact = "<Subject 1>は右手を対象面に押し当てる。"
        release = "<Subject 1>は右手を離す。"
        recover = "<Subject 1>は胴体を起こす。"
        blueprint = structures.LyricActionBlueprint(
            2, 1, "direct_subject_action", "対象面を示す。",
            (contact, release), "対象面に窪みが残る。",
        )
        shot = structures.PlannedShot(
            0, "<Subject 1>と対象面を捉える。",
            (prepare, contact[:-1], release, recover, recover[:-1]),
            "月光が差す。",
            structures.CameraPlan("push", "large", "fast", "前景から接近する。"),
        )
        scene = structures.PlannedScene(2, "押す。", (shot,))
        merged = planning._apply_lyric_action_blueprint(scene, blueprint)
        self.assertEqual(
            [policy.action_signature(value) for value in merged.shots[0].subject_actions],
            [policy.action_signature(value) for value in (prepare, contact, release, recover, blueprint.visible_result)],
        )

    def test_final_motion_repair_precedes_locked_result(self):
        result = "対象面に窪みが残る。"
        shot = structures.PlannedShot(
            0, "人物を示す。", ("<Subject 1>は右手を伸ばす。", result),
            "月光が差す。", structures.CameraPlan("static", "none", "none", "固定する。"),
        )
        scene = structures.PlannedScene(2, "触れる。", (shot,))
        repaired, additions = validation.repair_subject_motion(
            scene, duration_seconds=5, terminal_result=result,
        )
        self.assertTrue(additions)
        self.assertEqual(repaired.shots[0].subject_actions[-1], result)
        self.assertFalse(validation.subject_motion_issues(repaired, duration_seconds=5))

    def test_unquoted_speech_rejected_but_eating_not_treated_as_speech(self):
        response = lyric_blueprint().replace(
            "対象面に不均一な軌跡が残り、CLMPSUB1Xは指先を離す。",
            "口元が自然に広がり、永遠という言葉を口にする姿が確認できる",
        )
        recovered, errors = parse_lyric_blueprint(response)
        self.assertFalse(recovered)
        self.assertIn("forbidden vocal cue", errors[2])
        eating = lyric_blueprint().replace(
            "CLMPSUB1Xは対象面へ踏み込みながら片腕を引く。",
            "CLMPSUB1Xは果物を口にするために右手を持ち上げる。",
        )
        self.assertFalse(parse_lyric_blueprint(eating)[1])

    def test_final_repair_skips_a_template_already_in_the_shot(self):
        existing = "<Subject 1>は片足を一歩踏み出して重心を移し、胴体をひねりながら反対側の腕を大きく前へ振り出す。"
        shot = structures.PlannedShot(
            0, "人物を捉える。", (existing,), "夜。",
            structures.CameraPlan("static", "none", "none", "固定する。"),
        )
        repaired, additions = validation.repair_subject_motion(
            structures.PlannedScene(1, "動く。", (shot,)), duration_seconds=5,
        )
        self.assertEqual(len(additions), 1)
        self.assertNotIn(existing, additions)
        self.assertEqual(len(repaired.shots[0].subject_actions), 2)
        self.assertFalse(validation.subject_motion_issues(repaired, duration_seconds=5))

    def test_camera_copied_motion_repaired_in_all_scheduled_non_arc_types(self):
        action = "<Subject 1>の右手をゆっくりと上向きに上げる。"
        for scene_id, shot_count, index, expected in (
            (2, 1, 0, "接近"), (3, 1, 0, "後方"),
            (4, 1, 0, "水平移動"), (6, 1, 0, "上昇"),
            (5, 2, 1, "下降"),
        ):
            with self.subTest(scene_id=scene_id):
                requirement = camera_policy.required_camera_sequence(
                    scene_id=scene_id, profile_id="lyric_visuals_light_8b", shot_count=shot_count,
                )[index]
                repaired = camera_policy.repair_scheduled_camera_description(
                    "遠くから近づき、" + action,
                    requirement=requirement, actions=(action,),
                )
                self.assertNotIn("右手を", repaired)
                self.assertIn(expected, repaired)
                self.assertIn("前景", repaired)

    def test_usable_camera_route_is_preserved(self):
        description = "人物の右手を追いながら前景の柱の左側から後退し、背景の門まで見渡す。"
        requirement = camera_policy.required_camera_sequence(
            scene_id=3, profile_id="lyric_visuals_light_8b", shot_count=1,
        )[0]
        self.assertEqual(camera_policy.repair_scheduled_camera_description(
            description, requirement=requirement,
            actions=("<Subject 1>は右手を上げる。",),
        ), description)
