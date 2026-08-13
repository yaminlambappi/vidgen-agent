import unittest
from vidgen.models import FilmProject, Character, Location, Shot, FilmStatus, AudioPlan, EditPlan


class TestModels(unittest.TestCase):
    def test_film_project_creation(self):
        project = FilmProject(topic="The Future of AI")
        self.assertEqual(project.topic, "The Future of AI")
        self.assertEqual(project.status, FilmStatus.QUEUED)
        self.assertIsNotNone(project.project_id)

    def test_character_bible(self):
        char = Character(name="Alice", physical_description="Tall, with blue eyes")
        self.assertEqual(char.name, "Alice")
        self.assertEqual(char.physical_description, "Tall, with blue eyes")
        # Default fields should be empty strings
        self.assertEqual(char.personality, "")

    def test_shot_serialization(self):
        shot = Shot(
            scene_id="scene-1",
            index=1,
            shot_type="wide",
            subject="Robot",
            action="Walking",
            location_id="lab"
        )
        data = shot.model_dump()
        self.assertEqual(data["shot_type"], "wide")

        restored = Shot(**data)
        self.assertEqual(restored.shot_type, "wide")

    def test_audio_and_edit_plans(self):
        project = FilmProject(topic="Audio Test")
        project.edit_plan = EditPlan(sequence=["shot1", "shot2"])
        project.audio_plan = AudioPlan(narration_uri="gs://bucket/narration.mp3")
        self.assertEqual(len(project.edit_plan.sequence), 2)
        self.assertEqual(project.audio_plan.narration_uri, "gs://bucket/narration.mp3")


if __name__ == "__main__":
    unittest.main()
