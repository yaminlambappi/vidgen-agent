import unittest
from vidgen.models import StorySpec


class TestStorySpecRegression(unittest.TestCase):
    def test_mock_story_spec_has_title(self):
        """Regression: StorySpec should have all required attributes with defaults."""
        story = StorySpec()
        self.assertIsInstance(story, StorySpec)
        self.assertTrue(hasattr(story, 'title'))
        self.assertTrue(hasattr(story, 'logline'))
        self.assertTrue(hasattr(story, 'theme'))
        self.assertTrue(hasattr(story, 'genre'))
        # All default to empty string
        self.assertEqual(story.title, "")

    def test_story_spec_with_data(self):
        """StorySpec can be constructed with data."""
        story = StorySpec(
            title="Digital Dreams",
            logline="Bangladesh rises in the digital age.",
            theme="Technology and humanity",
            genre="Documentary"
        )
        self.assertEqual(story.title, "Digital Dreams")
        self.assertEqual(story.genre, "Documentary")


if __name__ == "__main__":
    unittest.main()
