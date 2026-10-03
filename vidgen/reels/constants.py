"""Immutable Reels factory constants. Duration is enforced in code, not prompts."""
from vidgen.config import settings

MAX_DURATION_SECONDS = float(settings.MAX_DURATION_SECONDS)
if MAX_DURATION_SECONDS <= 0 or MAX_DURATION_SECONDS > 30.0:
    MAX_DURATION_SECONDS = 30.0

REEL_WIDTH = int(settings.REEL_WIDTH)
REEL_HEIGHT = int(settings.REEL_HEIGHT)
REEL_ASPECT_RATIO = settings.REEL_ASPECT_RATIO
REEL_FPS = int(settings.REEL_FPS)
REEL_VIDEO_CODEC = "h264"
REEL_AUDIO_CODEC = "aac"

# Veo 3.x confirmed integer durations for text-to-video.
VEO_VALID_DURATIONS = tuple(settings.VEO_VALID_DURATIONS)

GENERIC_OPENINGS = (
    "in today's fast-paced world",
    "in todays fast-paced world",
    "in a world where",
    "are you tired of",
    "introducing the future",
    "unlock your potential",
    "elevate your lifestyle",
    "experience the difference",
    "in this day and age",
    "in today's modern world",
    "আজকের দ্রুতগতির বিশ্বে",
    "আজকের ব্যস্ত জীবনে",
    "আপনি কি ক্লান্ত",
)

CONTENT_MODES = (
    "UGC",
    "PRODUCT_DEMO",
    "TESTIMONIAL",
    "LIFESTYLE_COMMERCIAL",
    "DIRECT_RESPONSE_AD",
    "FOUNDER_STYLE",
    "STORYTELLING",
    "CINEMATIC_COMMERCIAL",
    "STREET_STYLE",
    "INTERVIEW",
    "COMEDY",
    "EMOTIONAL",
    "PROBLEM_SOLUTION",
    "BEFORE_AFTER",
    "PRODUCT_REVEAL",
)

HOOK_APPROACHES = (
    "curiosity",
    "problem",
    "surprise",
    "direct_statement",
    "visual_interruption",
    "question",
    "emotional_moment",
    "product_reveal",
    "relatable_situation",
    "transformation",
    "pattern_interrupt",
)
