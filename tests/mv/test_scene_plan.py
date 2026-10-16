"""Scene-state tests only: no renderer, model, inference, or delivered artwork."""
import copy
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from voice_motion.mv_scene_plan import PROJECT_ROOT, ScenePlanError, load_scene_plan, sample_scene, validate_scene_plan

EXAMPLE=PROJECT_ROOT/"examples"/"paper-star-15s.scene.json"


class DirectedSceneTests(unittest.TestCase):
    def setUp(self):
        self.scene=load_scene_plan(EXAMPLE)

    def test_fifteen_second_coverage_is_planning_not_a_rendered_artifact(self):
        report=validate_scene_plan(self.scene)
        self.assertTrue(report["valid"])
        self.assertEqual(report["frames"],360)
        self.assertFalse(report["render_ready"])
        self.assertFalse(report["artifact_success"])
        self.assertEqual(len(report["pending_assets"]),13)
        for frame,shot in [(0,"S01"),(95,"S01"),(96,"S02"),(215,"S02"),(216,"S03"),(359,"S03")]:
            sample=sample_scene(self.scene,frame)
            self.assertEqual(sample["shot_id"],shot)
            self.assertFalse(sample["rendered"])
            self.assertFalse(sample["artifact_success"])
            self.assertEqual(sample["quality_status"],"not_assessed")
        with self.assertRaises(ScenePlanError): load_scene_plan(EXAMPLE,require_render_ready=True)

    def test_star_is_physically_attached_until_one_continuous_release(self):
        hidden=sample_scene(self.scene,23)
        self.assertEqual(hidden["props"]["paper_star_01"]["state"],"pocket_hidden")
        for frame in [24,42,78,96,114,131]:
            sample=sample_scene(self.scene,frame)
            star=sample["props"]["paper_star_01"]
            grip=sample["actors"]["courier_01"]["anchors"]["right_grip"]
            self.assertEqual(star["attached_to"],{"actor":"courier_01","anchor":"right_grip"})
            self.assertLess(math.dist(star["world_xy"],grip),1e-8)
        before=sample_scene(self.scene,131); released=sample_scene(self.scene,132)
        self.assertIsNone(released["props"]["paper_star_01"]["attached_to"])
        self.assertEqual(released["props"]["paper_star_01"]["state"],"released")
        self.assertLess(math.dist(before["props"]["paper_star_01"]["world_xy"],released["props"]["paper_star_01"]["world_xy"]),1e-8)
        self.assertEqual(sample_scene(self.scene,114)["actors"]["courier_01"]["hand_open_fraction"],.5)
        flying=sample_scene(self.scene,168)
        self.assertGreater(math.dist(flying["props"]["paper_star_01"]["world_xy"],flying["actors"]["courier_01"]["anchors"]["right_grip"]),100)
        self.assertEqual(flying["props"]["paper_star_01"]["fold"],.5)

    def test_arm_has_fixed_lengths_instead_of_stretching_to_targets(self):
        for frame in [0,18,24,35,42,61,78,131,168,216,264,288,359]:
            arm=sample_scene(self.scene,frame)["actors"]["courier_01"]["right_arm"]
            self.assertAlmostEqual(math.dist(arm["shoulder"],arm["elbow"]),76,places=7)
            self.assertAlmostEqual(math.dist(arm["elbow"],arm["grip"]),78,places=7)

    def test_door_is_caused_by_the_same_released_star_and_camera_passes_only_after_opening(self):
        self.assertEqual(sample_scene(self.scene,215)["props"]["door_01"]["state"],"absent")
        beginning=sample_scene(self.scene,216)
        self.assertEqual(beginning["props"]["door_01"]["state"],"tracing")
        self.assertEqual(beginning["props"]["door_01"]["trace_progress"],0)
        dock=sample_scene(self.scene,228)
        self.assertEqual(dock["props"]["paper_star_01"]["state"],"door_seal")
        self.assertEqual(dock["props"]["paper_star_01"]["world_xy"],[720,140])
        middle=sample_scene(self.scene,240)
        self.assertGreater(middle["props"]["door_01"]["trace_progress"],0)
        self.assertEqual(middle["props"]["door_01"]["trace_origin_prop"],"paper_star_01")
        self.assertFalse(sample_scene(self.scene,288)["camera"]["passage_complete"])
        final=sample_scene(self.scene,348)
        self.assertTrue(final["camera"]["passage_complete"])
        self.assertEqual(final["props"]["door_01"]["state"],"open")
        self.assertEqual(final["actors"]["courier_01"]["gaze_target_prop"],"door_01")
        self.assertEqual(sample_scene(self.scene,359)["props"]["paper_star_01"]["state"],"door_seal")

    def test_teleport_release_invalid_precondition_and_unrelated_door_are_rejected(self):
        teleport=copy.deepcopy(self.scene)
        next(key for key in teleport["props"][0]["position_track"]["keys"] if key["frame"]==132)["value"]=[700,240]
        with self.assertRaisesRegex(ScenePlanError,"Detach position"): validate_scene_plan(teleport)
        missing_attachment=copy.deepcopy(self.scene)
        missing_attachment["events"]=missing_attachment["events"][1:]
        with self.assertRaisesRegex(ScenePlanError,"precondition"): validate_scene_plan(missing_attachment)
        unrelated=copy.deepcopy(self.scene); unrelated["props"][1]["trace"]["points"][0]=[710,140]
        with self.assertRaisesRegex(ScenePlanError,"same star"): validate_scene_plan(unrelated)
        premature=copy.deepcopy(self.scene)
        next(key for key in premature["props"][1]["tracks"]["trace_progress"]["keys"] if key["frame"]==216)["value"]=.1
        with self.assertRaises(ScenePlanError): validate_scene_plan(premature)

    def test_release_velocity_and_action_names_require_real_causal_channels(self):
        jerk=copy.deepcopy(self.scene)
        next(key for key in jerk["props"][0]["position_track"]["keys"] if key["frame"]==132)["tangent"]=[2,0]
        with self.assertRaisesRegex(ScenePlanError,"velocity"): validate_scene_plan(jerk)
        unchanged_fold=copy.deepcopy(self.scene)
        for key in unchanged_fold["props"][0]["tracks"]["fold"]["keys"]: key["value"]=0
        with self.assertRaisesRegex(ScenePlanError,"change both"): validate_scene_plan(unchanged_fold)
        closed_door=copy.deepcopy(self.scene)
        next(action for action in closed_door["shots"][2]["actions"] if action["kind"]=="camera_passage")["range"]=[264,288]
        with self.assertRaisesRegex(ScenePlanError,"closed door"): validate_scene_plan(closed_door)

    def test_automatic_selection_needs_the_actual_generated_candidate_in_its_log(self):
        scene=copy.deepcopy(self.scene); scene["stage"]="automatic_evaluation"
        scene["production_record"]["selected_by"]={"kind":"automatic","id":"selector","version":"test"}
        scene["production_record"]["candidate_log"]=[{"asset_id":"A00","candidate_id":"wrong","selected":True,"selection_kind":"automatic"}]
        scene["lyrics"]={"source":"model_transcription","timing_source":"asr_model_segment_timestamps"}
        scene["assets"][0]["provenance"]={"kind":"model_generated","model_id":"test-only","candidate_id":"actual"}
        with self.assertRaisesRegex(ScenePlanError,"candidate choice"): validate_scene_plan(scene)

    def test_mixed_numeric_track_types_and_pending_rights_cannot_render(self):
        scene=copy.deepcopy(self.scene)
        scene["shots"][0]["camera"]["zoom"]["keys"][1]["value"]=[1.1]
        with self.assertRaisesRegex(ScenePlanError,"scalar/vector"): validate_scene_plan(scene)
        with self.assertRaisesRegex(ScenePlanError,"Pending audio rights"):
            validate_scene_plan(self.scene,asset_root=EXAMPLE.parent,require_render_ready=True)

    def test_gap_overlap_fractional_frames_unknown_assets_and_channels_fail(self):
        mutations=[
            lambda s:s["shots"][1]["range"].__setitem__(0,97),
            lambda s:s["shots"][1]["range"].__setitem__(0,95),
            lambda s:s["shots"][1]["range"].__setitem__(0,96.),
            lambda s:s["actors"][0]["asset_ids"].append("unknown"),
            lambda s:s["shots"][0]["actions"][0].__setitem__("kind","sway_fallback"),
            lambda s:s["shots"][0]["actions"][0]["channels"].append("actors.courier_01.magic"),
            lambda s:s["assets"][0].__setitem__("id","A01"),
            lambda s:s["actors"][0]["anchor_tracks"]["right_grip"]["keys"][0].__setitem__("value",[1000,0]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                scene=copy.deepcopy(self.scene); mutation(scene)
                with self.assertRaises(ScenePlanError): validate_scene_plan(scene)
        for frame in [-1,360,1.5,True]:
            with self.assertRaises(ScenePlanError): sample_scene(self.scene,frame)

    def test_host_paths_urls_traversal_and_embedded_audio_are_rejected(self):
        for path in ["../outside.png","C:/Users/private.png","C:private.png","\\\\server\\private.png","/etc/private.png","https://example.invalid/art.png"]:
            scene=copy.deepcopy(self.scene); scene["assets"][0]["relative_asset"]=path
            with self.subTest(path=path):
                with self.assertRaises(ScenePlanError): validate_scene_plan(scene)
        scene=copy.deepcopy(self.scene); scene["audio"]["samples"]=[0.]
        with self.assertRaises(ScenePlanError): validate_scene_plan(scene)

    def test_human_art_study_cannot_be_relabelled_as_audio_only_automation(self):
        automatic=copy.deepcopy(self.scene); automatic["stage"]="automatic_evaluation"
        with self.assertRaisesRegex(ScenePlanError,"human-selected"): validate_scene_plan(automatic)
        automatic["production_record"]["selected_by"]={"kind":"automatic","id":"selector","version":"test"}
        automatic["production_record"]["candidate_log"]=[{"candidate_id":"test","selection_kind":"automatic"}]
        automatic["production_record"]["manual_edits"]=["human replaced an image"]
        with self.assertRaisesRegex(ScenePlanError,"manual edits"): validate_scene_plan(automatic)
        automatic["production_record"]["manual_edits"]=[]
        with self.assertRaisesRegex(ScenePlanError,"fixture/provided"): validate_scene_plan(automatic)

    def test_real_render_boundary_rejects_unreviewed_generated_file_even_when_hash_exists(self):
        temp_root=PROJECT_ROOT/".runtime"/"temp"; temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp:
            folder=Path(temp); scene=copy.deepcopy(self.scene)
            scene["audio"]["rights_status"]="owned"
            # Byte fixtures test integrity/review gates only; no placeholder art
            # is generated, rendered, committed, or described as a valid image.
            for entry in [scene["audio"],scene["assets"][0]]:
                path=folder/entry["relative_asset"]; path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b"test-only integrity bytes; not an image or audio output")
                entry["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
            asset=scene["assets"][0]
            asset["provenance"]={"kind":"model_generated","license":"test-license","model_id":"test-only","revision":"pinned-test","prompt":"test","seed":7,"candidate_id":"test"}
            with self.assertRaisesRegex(ScenePlanError,"Unreviewed/rejected"): validate_scene_plan(scene,asset_root=folder,require_render_ready=True)
            asset["review"]={"status":"approved","by":"test-reviewer"}; asset["provenance"].pop("revision")
            with self.assertRaisesRegex(ScenePlanError,"provenance is incomplete"): validate_scene_plan(scene,asset_root=folder,require_render_ready=True)
            asset["provenance"]["revision"]="test"; asset["sha256"]="0"*64
            with self.assertRaisesRegex(ScenePlanError,"hash mismatch"): validate_scene_plan(scene,asset_root=folder,require_render_ready=True)

    def test_samples_do_not_mutate_plan_or_claim_visual_quality(self):
        before=json.dumps(self.scene,sort_keys=True)
        sample=sample_scene(self.scene,0)
        sample["actors"]["courier_01"]["root"][0]=999
        sample["typography"]["avoid"].append("new")
        self.assertEqual(json.dumps(self.scene,sort_keys=True),before)
        self.assertNotIn("video_path",sample)
        self.assertNotIn("image_path",sample)

    def test_look_actions_reject_missing_eye_anchor_or_default_target_during_validation(self):
        for missing in [("eye_anchor",),("look_target",),("eye_anchor","look_target")]:
            scene=copy.deepcopy(self.scene)
            for key in missing: scene["actors"][0].pop(key)
            with self.subTest(missing=missing):
                with self.assertRaises(ScenePlanError): validate_scene_plan(scene)
                with self.assertRaises(ScenePlanError): sample_scene(scene,168)

    def test_camera_passage_requires_movement_and_a_final_interior_projection(self):
        stationary=copy.deepcopy(self.scene)
        for key in stationary["shots"][2]["camera"]["center"]["keys"]: key["value"]=[540,275]
        for key in stationary["shots"][2]["camera"]["zoom"]["keys"]: key["value"]=1
        with self.assertRaisesRegex(ScenePlanError,"actually move"): validate_scene_plan(stationary)
        incomplete=copy.deepcopy(self.scene)
        for key in incomplete["shots"][2]["camera"]["zoom"]["keys"]:
            if key["frame"]>=348: key["value"]=1.5
        with self.assertRaisesRegex(ScenePlanError,"final projection"): validate_scene_plan(incomplete)
        already_inside=copy.deepcopy(self.scene)
        for key in already_inside["shots"][2]["camera"]["center"]["keys"]: key["value"]=[720,280]
        for key in already_inside["shots"][2]["camera"]["zoom"]["keys"]: key["value"]=7+key["frame"]/360
        with self.assertRaisesRegex(ScenePlanError,"final projection"): validate_scene_plan(already_inside)
        valid=sample_scene(self.scene,348)["camera"]
        self.assertTrue(valid["passage_complete"])
        left,top,right,bottom=valid["portal_screen_bounds"]
        self.assertLessEqual(left,0); self.assertLessEqual(top,0)
        self.assertGreaterEqual(right,960); self.assertGreaterEqual(bottom,540)

    def test_semantic_fraction_keys_and_interpolated_values_stay_finite_and_bounded(self):
        cases=[("actor","hand_open_fraction",114,2),
               ("star","fold",168,1.2),
               ("star","visible_fraction",42,-.1),
               ("door","open_fraction",288,2),
               ("door","trace_progress",264,2),
               ("actor","hand_open_fraction",114,float("nan"))]
        for entity,channel,frame,value in cases:
            scene=copy.deepcopy(self.scene)
            owner=scene["actors"][0] if entity=="actor" else scene["props"][0 if entity=="star" else 1]
            next(key for key in owner["tracks"][channel]["keys"] if key["frame"]==frame)["value"]=value
            with self.subTest(entity=entity,channel=channel,value=value):
                with self.assertRaises(ScenePlanError): validate_scene_plan(scene)
        overshoot=copy.deepcopy(self.scene)
        next(key for key in overshoot["actors"][0]["tracks"]["hand_open_fraction"]["keys"] if key["frame"]==96)["tangent"]=10
        with self.assertRaisesRegex(ScenePlanError,"interpolation"): validate_scene_plan(overshoot)


if __name__=="__main__": unittest.main()
