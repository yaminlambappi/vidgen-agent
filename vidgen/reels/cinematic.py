"""Cinematic director: places and lines from the idea, not 'Watch this for one second'."""
from __future__ import annotations

from typing import List, Tuple


def _low(idea: str) -> str:
    return (idea or "").lower()


def is_rain_idea(idea: str) -> bool:
    blob = _low(idea)
    return any(k in blob for k in (
        "monsoon", "rain", "storm", "downpour", "বর্ষা", "বৃষ্টি", "kalboishakhi",
    ))


def is_dhaka_idea(idea: str) -> bool:
    blob = _low(idea)
    return any(k in blob for k in ("dhaka", "ঢাকা", "bangladesh", "bangladeshi", "old town", "motijheel"))


def cinematic_hooks(idea: str, language: str) -> dict:
    rain = is_rain_idea(idea)
    dhaka = is_dhaka_idea(idea)
    bn = str(language).startswith("bengali")
    if rain and dhaka:
        if bn:
            return {
                "curiosity": ("ঢাকায় বৃষ্টি অনুমতি চায় না।", "First heavy drops on a tin roof, then a face."),
                "relatable_situation": ("রাস্তা আগে ভেজে, তারপর মানুষ।", "Ankles in brown water, no panic."),
                "direct_statement": ("এই শহর দৌড়ায় না। মানিয়ে নেয়।", "Shared umbrella, two strangers."),
                "question": ("আজকের বৃষ্টি কি থামবে?", "Puddle holding neon."),
            }
        return {
            "curiosity": ("Rain in Dhaka does not ask permission.", "First heavy drops on a tin roof, then a face."),
            "relatable_situation": ("The street gets wet before the people do.", "Ankles in brown water, no panic."),
            "direct_statement": ("This city does not run. It adjusts.", "Shared umbrella, two strangers."),
            "question": ("Will it stop before evening?", "A puddle holding neon."),
        }
    if rain:
        if bn:
            return {
                "curiosity": ("আকাশটা একবার ভাঙল।", "Rain hitting a window, already mid-storm."),
                "relatable_situation": ("সবাই এক ছাউনির নিচে দাঁড়াল।", "Bodies under a shop awning."),
                "direct_statement": ("বৃষ্টি এলে শহর আস্তে হয়।", "Wipers, a still face."),
            }
        return {
            "curiosity": ("The sky broke once, then kept going.", "Rain on glass, already mid-storm."),
            "relatable_situation": ("Everyone found the same awning.", "Bodies under a shop roof."),
            "direct_statement": ("When it rains the city gets quieter.", "Wipers, a still face."),
        }
    if dhaka:
        if bn:
            return {
                "curiosity": ("ঢাকা থামে না। শুধু গতি বদলায়।", "A street already in motion."),
                "relatable_situation": ("চা-এর কাপে শহরটা এক সেকেন্ড থামে।", "Hands around a glass, traffic behind."),
            }
        return {
            "curiosity": ("Dhaka does not stop. It only changes speed.", "A street already in motion."),
            "relatable_situation": ("The city pauses only for a glass of tea.", "Hands around a glass, traffic behind."),
        }
    topic = (idea or "this place").strip()[:60]
    if bn:
        return {
            "curiosity": (f"{topic} — প্রথম ফ্রেমেই শুরু।", "A face or a specific object already in motion."),
            "direct_statement": ("কথা কম। ছবি বলুক।", "Still, honest framing."),
        }
    return {
        "curiosity": (f"{topic} — already happening in frame one.", "A face or a specific object already in motion."),
        "direct_statement": ("Fewer words. Let the picture speak.", "Still, honest framing."),
    }


def cinematic_script(idea: str, language: str) -> List[str]:
    rain = is_rain_idea(idea)
    dhaka = is_dhaka_idea(idea)
    bn = str(language).startswith("bengali")
    if rain and dhaka:
        if bn:
            return [
                "ঢাকায় বৃষ্টি অনুমতি চায় না।",
                "আগে রাস্তা ভেজে, তারপর চায়ের দোকানের ছাউনি।",
                "কেউ দৌড়ায় না। সবাই মানিয়ে নেয়।",
                "এই শহরটা এমনই।",
            ]
        return [
            "Rain in Dhaka does not ask permission.",
            "It takes the street first, then the tea-stall roof.",
            "People do not run. They adjust.",
            "That is the city.",
        ]
    if rain:
        if bn:
            return [
                "আকাশ ভাঙল, কিন্তু কেউ অবাক নয়।",
                "এক ছাউনির নিচে অচেনা মানুষ।",
                "বৃষ্টি থামলে সবাই আবার নিজের পথে।",
            ]
        return [
            "The sky broke. Nobody was surprised.",
            "Strangers share one awning.",
            "When it stops, everyone walks their own way again.",
        ]
    if dhaka:
        if bn:
            return [
                "ঢাকা থামে না।",
                "চা-এর এক চুমুকে শহর এক সেকেন্ড শান্ত।",
                "তারপর আবার গতি।",
            ]
        return [
            "Dhaka does not stop.",
            "A sip of tea is the only quiet.",
            "Then the city moves again.",
        ]
    if bn:
        return [
            "প্রথম ফ্রেমেই ঘটনা চলছে।",
            "কথা কম।",
            "শেষ সেকেন্ডে থামো।",
        ]
    return [
        "It is already happening in the first frame.",
        "Keep the words few.",
        "Hold the last second.",
    ]


def cinematic_places(idea: str, n: int) -> List[str]:
    rain = is_rain_idea(idea)
    dhaka = is_dhaka_idea(idea)
    if rain and dhaka:
        bank = [
            "Dhaka window, first heavy drops, late afternoon",
            "flooded street at ankle height, brown water, no panic",
            "CNG wipers fighting the rain, both faces in glass",
            "tea stall under a tin roof, steam and rain noise",
            "cycle-rickshaw vinyl cover flapping, wet road",
            "rooftop clothesline, soaked shirts, open sky",
            "two strangers under one cheap umbrella",
            "neon shop sign broken in a puddle",
            "hands around a glass of cha, rain in the background",
            "wet concrete rooftop, city going dim",
            "last wide of Gulistan or Old Dhaka in the rain, hold",
        ]
    elif rain:
        bank = [
            "window glass, rain already heavy",
            "shop awning, people packed in",
            "car wipers, a still passenger",
            "empty street, rain bouncing",
            "hands wringing a wet sleeve",
            "sky just after the break",
        ]
    elif dhaka:
        bank = [
            "Motijheel or Old Dhaka street, late light",
            "tea stall, real glasses, traffic behind",
            "rickshaw in a jam, close on the driver",
            "rooftop water tank, city haze",
            "a doorway, someone waiting",
        ]
    else:
        bank = [
            "a real room already in use, not an establishing empty set",
            "a doorway or threshold, late light",
            "hands on a specific object from the idea",
            "a face, then the place",
        ]
    if n <= 0:
        return []
    out = []
    for i in range(n):
        out.append(bank[i] if i < len(bank) else bank[-(1 + (i % 2))])
    return out


def cinematic_beats(n: int) -> List[str]:
    spine = [
        "hook", "weather", "street", "vehicle", "shelter",
        "rooftop", "strangers", "reflection", "tea", "hold", "payoff",
    ]
    if n <= 1:
        return ["hook"]
    if n <= len(spine):
        return [spine[0], *spine[1:n - 1], "payoff"]
    extra = ["street", "shelter", "hold"]
    mid = spine[1:-1]
    out = [spine[0]]
    i = 0
    while len(out) < n - 1:
        out.append(mid[i % len(mid)] if i < len(mid) else extra[i % len(extra)])
        i += 1
    out.append("payoff")
    return out


def cinematic_action(place: str, purpose: str, name: str) -> str:
    return (
        f"THIRD-PERSON. FIRST FRAME is already in the weather or the street, never an empty apartment. "
        f"Place: {place}. {name} is small in the frame or seen in glass, not presenting. "
        f"Beat: {purpose}. Real rain or real city texture. No drone hero shot. No studio sweep."
    )


def cinematic_camera(index: int, n: int) -> Tuple[str, str, str]:
    if index == 0:
        return (
            "locked or 10cm drift, close on rain hitting tin or glass — NEVER a selfie",
            "9:16, FIRST FRAME is weather or a face in weather. No empty table.",
            "overcast Dhaka light, wet surfaces, no beauty dish",
        )
    if index >= n - 1:
        return (
            "locked wide, hold, no crane",
            "9:16, city or rooftop, let it sit",
            "late storm light, no sunset filter",
        )
    cycle = (
        ("medium from a doorway, rain beyond", "9:16, one person, real depth", "available overcast"),
        ("close on hands or wipers", "9:16, texture first", "practical stall light"),
        ("wide street, low, water in foreground", "9:16, ankles and traffic", "flat monsoon sky"),
    )
    return cycle[index % 3]
