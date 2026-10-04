"""School of Sufi path: one prayer, one world, a 30-second file that passed the gates."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vidgen.config import settings
from vidgen.models import GenerationJob
from vidgen.providers.storage import MockStorageProvider
from vidgen.sufi.engine import _hear, generate
from vidgen.sufi.ledger import CostGuardTripped, Ledger
from vidgen.sufi.plan import (
    PlanError,
    _OFFLINE_BEATS,
    _OFFLINE_BIBLE,
    choose_duration,
    offline_plan,
    parse_plan,
    prompts_share_identity,
    slot_durations,
    speech_seconds,
)
from vidgen.sufi.publish import publish_video
from vidgen.sufi.render import (
    PronunciationError,
    QualityError,
    assert_film,
    assert_reel,
    blackframe_count,
    build_tts_request,
    caption_faults,
    check_pronunciation,
    fit_phrase,
    inspect_shots,
    is_flicker,
    opening_is_static,
    render_ducked_bed,
    volume_levels,
    write_captions,
    write_drone,
    write_flicker,
    write_plate,
    write_pulsed_voice,
    write_solid,
    write_tone_voice,
)


def _beats():
    return [
        {
            "phrase": phrase,
            "glyph": glyph,
            "emotion": emotion,
            "visual_intention": intention,
            "veo_prompt": action,
        }
        for phrase, glyph, emotion, intention, action in _OFFLINE_BEATS
    ]


def _payload():
    return {
        "emotional_core": "a hidden loneliness",
        "spiritual_direction": [
            "loneliness", "calling", "ache", "surrender", "nearness", "peace",
        ],
        "visual_bible": dict(_OFFLINE_BIBLE),
        "beats": _beats(),
        "caption_and_hashtags": "একটি নিভৃত মোনাজাত।",
    }


class TestDuration(unittest.TestCase):
    def test_every_thought_is_six_shots_of_five_seconds(self):
        self.assertEqual(choose_duration("A quiet return."), 30)
        self.assertEqual(choose_duration("word " * 80), 30)
        self.assertEqual(slot_durations(30, 6), [5, 5, 5, 5, 5, 5])
        with self.assertRaises(PlanError):
            slot_durations(30, 5)
        with self.assertRaises(PlanError):
            slot_durations(60, 10)


class TestPlan(unittest.TestCase):
    def test_locked_prompts_carry_the_whole_identity(self):
        plan = parse_plan(_payload(), 30)
        self.assertEqual([beat.role for beat in plan.beats], [
            "arrival", "ache", "longing", "surrender", "nearness", "release",
        ])
        self.assertTrue(prompts_share_identity(plan.veo_prompts, plan.visual_bible))
        self.assertIn("#SchoolOfSufi", plan.caption_and_hashtags)
        self.assertIn("মাওলা", plan.script_text)
        self.assertNotIn("মৌলা", plan.script_text)
        self.assertIn("first 400 milliseconds", plan.veo_prompts[0])
        self.assertIn("calm final frame", plan.veo_prompts[5])

    def test_world_name_alone_is_not_continuity(self):
        plan = offline_plan("একটি কথা", 30)
        world_only = [f"{plan.visual_bible.world} and nothing else" for _ in range(6)]
        self.assertFalse(prompts_share_identity(world_only, plan.visual_bible))
        data = _payload()
        data["visual_bible"] = dict(_OFFLINE_BIBLE)
        data["visual_bible"]["environment"] = ""
        with self.assertRaises(PlanError):
            parse_plan(data, 30)

    def test_repeated_emotion_still_opening_and_unresolved_ending_are_rejected(self):
        same = _payload()
        same["beats"][1]["emotion"] = same["beats"][0]["emotion"]
        with self.assertRaises(PlanError):
            parse_plan(same, 30)

        still = _payload()
        still["beats"][0]["veo_prompt"] = "A fade from black, then a slow dolly toward the lamp."
        with self.assertRaises(PlanError):
            parse_plan(still, 30)

        ending = _payload()
        ending["beats"][5]["emotion"] = "wonder"
        with self.assertRaises(PlanError):
            parse_plan(ending, 30)

    def test_language_and_length_are_spoken_bangla(self):
        english = _payload()
        english["beats"][0]["phrase"] = "The heart grows quiet when remembrance is sincere."
        with self.assertRaises(PlanError):
            parse_plan(english, 30)

        collapsed = _payload()
        collapsed["beats"][0]["phrase"] = "মৌলা, তুমি জানো।"
        with self.assertRaises(PlanError):
            parse_plan(collapsed, 30)

        lecture = _payload()
        lecture["beats"][0]["phrase"] = "মাওলা, এই রাতের প্রথম আলো তোমার। " * 6
        self.assertGreater(speech_seconds(lecture["beats"][0]["phrase"]), 5)
        with self.assertRaises(PlanError):
            parse_plan(lecture, 30)

        short = _payload()
        short["beats"][0]["phrase"] = "মাওলা, তুমি জানো।"
        plan = parse_plan(short, 30)
        self.assertLessEqual(speech_seconds(plan.beats[0].phrase), 5)

    def test_one_beat_is_rejected(self):
        data = _payload()
        data["beats"] = data["beats"][:1]
        with self.assertRaises(PlanError):
            parse_plan(data, 30)


class TestVoiceRequest(unittest.TestCase):
    def test_request_uses_configured_voice_rate_pitch_and_break(self):
        with patch.object(settings, "TTS_VOICE", "bn-IN-Wavenet-B"), \
             patch.object(settings, "TTS_SPEAKING_RATE", 0.5), \
             patch.object(settings, "TTS_PITCH", -3.0), \
             patch.object(settings, "TTS_MAWLA_BREAK_MS", 90), \
             patch.object(settings, "TTS_LANGUAGE", "bn-IN"):
            request = build_tts_request("Maula, তুমি জানো।")
        self.assertEqual(request["voice"], "bn-IN-Wavenet-B")
        self.assertEqual(request["speaking_rate"], 0.5)
        self.assertEqual(request["pitch"], -3.0)
        self.assertEqual(request["language"], "bn-IN")
        self.assertIn('rate="50%"', request["ssml"])
        self.assertIn('pitch="-3st"', request["ssml"])
        self.assertIn('time="90ms"', request["ssml"])
        self.assertIn("মা<break", request["ssml"])
        self.assertIn("ওলা", request["ssml"])
        self.assertNotIn("মৌলা", request["ssml"])
        self.assertNotIn("Maula", request["ssml"])

    def test_heard_moula_is_rejected_and_mawla_is_accepted(self):
        with self.assertRaises(PronunciationError):
            check_pronunciation("মাওলা, তুমি জানো।", "মৌলা, তুমি জানো।")
        check_pronunciation("মাওলা, তুমি জানো।", "মাওলা, তুমি জানো।")
        with self.assertRaises(PronunciationError):
            build_tts_request("মৌলা")

    def test_a_wrong_transcript_is_heard_again_once(self):
        calls = {"n": 0}

        def recognizer(path, phrase):
            calls["n"] += 1
            return "মৌলা" if calls["n"] == 1 else "মাওলা, শান্তি"

        def tts(phrase, path):
            write_tone_voice(path, 1)

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.m4a"
            fitted = Path(tmp) / "fitted.m4a"
            write_tone_voice(str(raw), 1)
            fit_phrase(str(raw), str(fitted), 5)
            heard = _hear("মাওলা, শান্তি", fitted, raw, Ledger(), tts, recognizer, True)
        self.assertEqual(heard, "passed")
        self.assertEqual(calls["n"], 2)

        def always_wrong(path, phrase):
            return "মৌলা"

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.m4a"
            fitted = Path(tmp) / "fitted.m4a"
            write_tone_voice(str(raw), 1)
            fit_phrase(str(raw), str(fitted), 5)
            with self.assertRaises(PronunciationError):
                _hear("মাওলা, শান্তি", fitted, raw, Ledger(), tts, always_wrong, True)


class TestAmbience(unittest.TestCase):
    def test_a_pause_does_not_gate_the_bed(self):
        with tempfile.TemporaryDirectory() as tmp:
            voice = Path(tmp) / "voice.m4a"
            bed = Path(tmp) / "bed.m4a"
            ducked = Path(tmp) / "ducked.m4a"
            write_pulsed_voice(str(voice), 6)
            write_drone(str(bed), 6)
            render_ducked_bed(str(voice), str(bed), str(ducked), 6)
            under, _ = volume_levels(str(ducked), start=0.2, duration=1.5)
            gap, _ = volume_levels(str(ducked), start=2.3, duration=1.4)
        self.assertGreater(gap, -40)
        self.assertGreater(gap, under - 8)
        self.assertLess(abs(gap - under), 12)


class TestPictures(unittest.TestCase):
    def test_local_frame_checks_catch_a_still_a_black_frame_and_flicker(self):
        with tempfile.TemporaryDirectory() as tmp:
            still = Path(tmp) / "still.mp4"
            black = Path(tmp) / "black.mp4"
            flicker = Path(tmp) / "flicker.mp4"
            moving = Path(tmp) / "moving.mp4"
            write_solid(str(still), 2)
            write_solid(str(black), 1, "black")
            write_flicker(str(flicker), 1)
            write_plate(str(moving), 2)
            self.assertTrue(opening_is_static(str(still)))
            self.assertGreater(blackframe_count(str(black)), 0)
            self.assertTrue(is_flicker(str(flicker)))
            self.assertFalse(opening_is_static(str(moving)))
            self.assertFalse(is_flicker(str(moving)))
            findings = inspect_shots([str(still), str(black), str(flicker)])
        codes = {item["index"]: item["codes"] for item in findings}
        self.assertIn("static_opening", codes[0])
        self.assertIn("black_frame", codes[1])
        self.assertIn("flicker", codes[2])

    def test_identity_break_reshoots_once_and_does_not_upload(self):
        class Gen:
            def __init__(self):
                self.n = 0

            def generate_shot(self, **_kwargs):
                self.n += 1
                return GenerationJob(status="completed", artifact_uri="")

        gen = Gen()
        uploads = []

        def reviewer(_paths, _bible):
            return [{"index": 0, "codes": ["identity_break"]}]

        def track(self, local, remote):
            uploads.append(remote)
            return f"gs://mock/{remote}"

        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False), \
             patch.object(settings, "MAX_VEO_CALLS", 12), \
             patch.object(MockStorageProvider, "upload", track):
            with self.assertRaises(QualityError) as caught:
                generate("একটি নিভৃত কথা", video_gen=gen, reviewer=reviewer, publish=False)
        self.assertIn("identity_break", str(caught.exception))
        self.assertEqual(gen.n, 7)
        self.assertEqual(uploads, [])


class TestCaptions(unittest.TestCase):
    def test_glyphs_sit_on_five_second_boundaries(self):
        glyphs = ["মাওলা", "ক্লান্ত হৃদয়", "আকুতি", "সমর্পণ", "নীরবতা", "শান্তি"]
        phrases = [row[0] for row in _OFFLINE_BEATS]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "captions.ass"
            write_captions(glyphs, str(path))
            self.assertEqual(caption_faults(str(path), glyphs, phrases), [])
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(phrases[1], text)
            broken = text.replace("0:00:05.00", "0:00:04.00", 1)
            bad = Path(tmp) / "bad.ass"
            bad.write_text(broken, encoding="utf-8")
            faults = caption_faults(str(bad), glyphs, phrases)
        self.assertTrue(any("mistimed" in fault for fault in faults))


class TestLedger(unittest.TestCase):
    def test_veo_ceiling_trips_before_the_call(self):
        ledger = Ledger()
        with patch.object(settings, "MAX_VEO_CALLS", 1):
            ledger.charge("veo")
            with self.assertRaises(CostGuardTripped):
                ledger.charge("veo")


class TestPublish(unittest.TestCase):
    def test_unconfigured_targets_are_skipped(self):
        with patch.object(settings, "YOUTUBE_CLIENT_ID", ""), \
             patch.object(settings, "YOUTUBE_CLIENT_SECRET", ""), \
             patch.object(settings, "YOUTUBE_REFRESH_TOKEN", ""), \
             patch.object(settings, "SOCIAL_WEBHOOK_URL", ""):
            report = publish_video("/tmp/does-not-need-to-exist.mp4", "A quiet heart.", "caption", 30)
        self.assertEqual(report["youtube"]["status"], "skipped")
        self.assertEqual(report["webhook"]["status"], "skipped")


class TestReel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False), \
             patch.object(settings, "YOUTUBE_CLIENT_ID", ""), \
             patch.object(settings, "YOUTUBE_CLIENT_SECRET", ""), \
             patch.object(settings, "YOUTUBE_REFRESH_TOKEN", ""), \
             patch.object(settings, "SOCIAL_WEBHOOK_URL", ""), \
             patch("vidgen.sufi.engine._gemini_text") as gemini, \
             patch("vidgen.sufi.engine._client") as client, \
             patch("vidgen.sufi.render.cloud_tts") as tts, \
             patch("vidgen.sufi.render.transcribe_bangla") as stt, \
             patch("vidgen.providers.video.VeoVideoGenerator.generate_shot") as veo:
            cls.result = generate("Sincerity is the hidden root of every act of remembrance.", publish=True)
            cls.paid = (gemini, client, tts, stt, veo)

    def test_simulation_ships_one_munajat_without_paid_providers(self):
        result = self.result
        for paid in self.paid:
            paid.assert_not_called()
        self.assertEqual(result.duration_seconds, 30)
        self.assertEqual(result.slots, [5, 5, 5, 5, 5, 5])
        self.assertEqual([beat["role"] for beat in result.beats], [
            "arrival", "ache", "longing", "surrender", "nearness", "release",
        ])
        self.assertEqual(result.plan_source, "offline_draft")
        self.assertEqual(result.visual_qa, "local_only")
        self.assertEqual(result.voice_qa, "skipped")
        self.assertEqual(result.ledger["veo"], 6)
        self.assertEqual(result.ledger["tts"], 0)
        self.assertEqual(result.ledger["gemini"], 0)
        self.assertIn("মাওলা", result.script_text)
        self.assertTrue(result.gcs_video_uri.endswith(f"sufi/{result.job_id}/final_short.mp4"))
        self.assertEqual(len(result.shots), 6)
        for index, shot in enumerate(result.shots, start=1):
            self.assertIn(f"sufi/{result.job_id}/shots/shot_{index}.mp4", shot["gcs_uri"])
        self.assertEqual(result.publish["youtube"]["status"], "skipped")
        self.assertEqual(result.probe["width"], 1080)
        self.assertEqual(result.probe["height"], 1920)
        self.assertAlmostEqual(result.probe["duration"], 30, delta=0.15)
        self.assertTrue(prompts_share_identity(
            result.veo_prompts,
            offline_plan(result.thought, 30).visual_bible,
        ))

    def test_assert_film_rejects_a_broken_master(self):
        result = self.result
        root = Path(result.video_path).parent
        with self.assertRaises(QualityError):
            assert_film(
                str(root / "shot_1.mp4"),
                segments=[str(root / f"shot_{i}.mp4") for i in range(1, 7)],
                voice_path=str(root / "voice.m4a"),
                bed_path=str(root / "bed.m4a"),
                captions_path=str(root / "captions.ass"),
                glyphs=[beat["glyph"] for beat in result.beats],
                phrases=[beat["phrase"] for beat in result.beats],
            )

        silent = root / "silent.m4a"
        write_tone_voice(str(silent), 1)
        # A one-second tone padded across the film is not the failure we want;
        # a true silent stem is.
        from vidgen.sufi.render import ffmpeg, _run
        _run([
            ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", "30", "-c:a", "aac", str(silent),
        ])
        with self.assertRaises(QualityError) as silent_case:
            assert_film(
                result.video_path,
                segments=[str(root / f"shot_{i}.mp4") for i in range(1, 7)],
                voice_path=str(silent),
                bed_path=str(root / "bed.m4a"),
                captions_path=str(root / "captions.ass"),
                glyphs=[beat["glyph"] for beat in result.beats],
                phrases=[beat["phrase"] for beat in result.beats],
            )
        self.assertIn("no speech", str(silent_case.exception))

        captions = (root / "captions.ass").read_text(encoding="utf-8").replace("0:00:05.00", "0:00:03.00", 1)
        mistimed = root / "mistimed.ass"
        mistimed.write_text(captions, encoding="utf-8")
        with self.assertRaises(QualityError) as caption_case:
            assert_film(
                result.video_path,
                segments=[str(root / f"shot_{i}.mp4") for i in range(1, 7)],
                voice_path=str(root / "voice.m4a"),
                bed_path=str(root / "bed.m4a"),
                captions_path=str(mistimed),
                glyphs=[beat["glyph"] for beat in result.beats],
                phrases=[beat["phrase"] for beat in result.beats],
            )
        self.assertIn("mistimed", str(caption_case.exception))

        black = root / "black.mp4"
        write_solid(str(black), 2, "black")
        self.assertGreater(blackframe_count(str(black)), 0)
        with self.assertRaises(QualityError) as black_case:
            assert_film(
                str(black),
                segments=[str(root / f"shot_{i}.mp4") for i in range(1, 7)],
                voice_path=str(root / "voice.m4a"),
                bed_path=str(root / "bed.m4a"),
                captions_path=str(root / "captions.ass"),
                glyphs=[beat["glyph"] for beat in result.beats],
                phrases=[beat["phrase"] for beat in result.beats],
            )
        self.assertIn("black frames", str(black_case.exception))

        clipped = root / "clipped.mp4"
        _run([
            ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", result.video_path,
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=30,volume=8",
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(clipped),
        ])
        _mean, peak = volume_levels(str(clipped))
        self.assertGreater(peak, -0.5)
        with self.assertRaises(QualityError) as clip_case:
            assert_film(
                str(clipped),
                segments=[str(root / f"shot_{i}.mp4") for i in range(1, 7)],
                voice_path=str(root / "voice.m4a"),
                bed_path=str(root / "bed.m4a"),
                captions_path=str(root / "captions.ass"),
                glyphs=[beat["glyph"] for beat in result.beats],
                phrases=[beat["phrase"] for beat in result.beats],
            )
        self.assertIn("peaks", str(clip_case.exception))

    def test_tiny_file_fails_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mp4"
            path.write_bytes(b"not a video")
            with self.assertRaises(Exception):
                assert_reel(str(path), 30)

    def test_plate_is_vertical(self):
        from vidgen.sufi.render import probe
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plate.mp4"
            write_plate(str(path), 1)
            info = probe(str(path))
        self.assertEqual((info["width"], info["height"]), (1080, 1920))


class TestCritic(unittest.TestCase):
    def test_a_failed_review_regenerates_before_any_picture(self):
        calls = []

        def llm(thought, duration, prompt_count, correction=""):
            calls.append(correction)
            if not correction:
                return _payload()
            return {"emotional_core": "still not a film"}

        class Gen:
            def generate_shot(self, **_kwargs):
                raise AssertionError("video generator was called")

        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False), \
             patch.object(settings, "MAX_GEMINI_CALLS", 6):
            with self.assertRaises(PlanError):
                generate(
                    "একটি নিভৃত কথা আছে",
                    llm=llm,
                    critic=lambda _plan: {"pass": False, "reasons": ["not one film"]},
                    video_gen=Gen(),
                    publish=False,
                )
        self.assertEqual(len(calls), 2)
        self.assertIn("not one film", calls[1])


class TestApi(unittest.TestCase):
    def test_generate_route_returns_the_short(self):
        from fastapi.testclient import TestClient
        import main

        payload = {
            "job_id": "job",
            "thought": "A quiet heart.",
            "duration_seconds": 30,
            "script_text": "A quiet heart returns.",
            "veo_prompts": ["a", "b"],
            "caption_and_hashtags": "#Sufism #SpiritualReminders #Tasawwuf #SchoolOfSufi",
            "slots": [15, 15],
            "video_path": "/tmp/short.mp4",
            "plan_source": "llm",
            "voice_source": "tone_fallback",
            "shots": [],
            "ledger": {"veo": 2, "tts": 1, "gemini": 1, "events": []},
            "publish": {},
            "probe": {},
        }

        class _Result:
            def model_dump(self):
                return payload

        with patch("main.generate", return_value=_Result()):
            client = TestClient(main.app)
            response = client.post("/generate", json={"thought": "A quiet heart."})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["duration_seconds"], 30)
        self.assertEqual(sum(body["slots"]), 30)

    def test_empty_thought_is_rejected(self):
        from fastapi.testclient import TestClient
        import main
        client = TestClient(main.app)
        response = client.post("/generate", json={"thought": "no"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
