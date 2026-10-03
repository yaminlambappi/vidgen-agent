"""Comedy scripts people would actually forward. Not the name/very-good gag."""
from __future__ import annotations

import hashlib
import re
from typing import List, Sequence, Tuple

from vidgen.reels.language import has_bengali


LAME_COMEDY = (
    "আপনার নাম কী",
    "জি… খুব ভালো",
    "জি খুব ভালো",
    "প্রশ্নটা আবার",
    "what is your name",
    "yes. very good",
    "yes very good",
    "one more time",
    "পুরা কনফিডেন্ট",
    "আমি একদম ready",
    "i am extremely prepared",
    "স্যার, আমি একদম ready",
)

_SITUATIONS = (
    ("interview", ("interview", "ইন্টারভিউ", "job interview", "চাকরি", "candidate", "hr")),
    ("traffic", ("traffic", "jam", "রিকশা", "cng", "uber", "pathao", "জাব", "রাস্তা")),
    ("office", ("office", "boss", "বস", "deadline", "ডেডলাইন", "salary", "বেতন")),
    ("home", ("mother", "mom", "মা", "বাসা", "phone call", "ফোন")),
    ("exam", ("exam", "পরীক্ষা", "test", "assignment")),
)


def detect_situation(idea: str) -> str:
    low = (idea or "").lower()
    for name, keys in _SITUATIONS:
        if any(k in low for k in keys):
            return name
    return "office"


def is_lame_comedy(text: str) -> bool:
    blob = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not blob:
        return True
    return any(p in blob for p in LAME_COMEDY)


def _seed(idea: str) -> int:
    return int(hashlib.sha256((idea or "joke").encode()).hexdigest()[:8], 16)


def _pick(bank: Sequence[Sequence[str]], idea: str) -> List[str]:
    return list(bank[_seed(idea) % len(bank)])


def comedy_cast(situation: str, bd: bool) -> Tuple[Tuple[str, str, str], Tuple[str, str, str]]:
    """(name, role, wardrobe) for lead, other."""
    if situation == "interview":
        return (
            ("Rafi" if bd else "Ray", "candidate", "slightly tight white shirt, cheap dark tie, nervous crease"),
            ("Kabir" if bd else "Hassan", "interviewer", "plain pale office shirt, no tie, tired eyes"),
        )
    if situation == "traffic":
        return (
            ("Rafi" if bd else "Ray", "passenger", "weekday shirt, no tie, phone in the lap"),
            ("Kabir" if bd else "Hassan", "driver", "faded shirt, tired eyes, hands on the wheel"),
        )
    if situation == "home":
        return (
            ("Rafi" if bd else "Ray", "son", "home t-shirt, no costume"),
            ("Nusrat" if bd else "Maya", "mother", "printed cotton, phone in hand"),
        )
    if situation == "exam":
        return (
            ("Rafi" if bd else "Ray", "student", "plain shirt, exam-day hair"),
            ("Kabir" if bd else "Hassan", "friend", "same hall clothes, flatter affect"),
        )
    return (
        ("Rafi" if bd else "Ray", "junior", "office shirt, sleeves rolled once"),
        ("Kabir" if bd else "Hassan", "boss", "plain office shirt, no tie, tired eyes"),
    )


def comedy_location(situation: str) -> str:
    return {
        "interview": "small Dhaka office, cheap desk, fluorescent tubes, two chairs facing each other",
        "traffic": "Dhaka CNG stuck in a jam, both faces, locked camera on the dash",
        "office": "open Dhaka office, cheap partitions, fluorescent, two chairs at one desk",
        "home": "Dhaka living room, afternoon light, phone on the table",
        "exam": "exam-hall corridor, ugly tube light, two students",
    }.get(situation, "everyday Dhaka room, locked two-shot, both faces")


def comedy_action(situation: str, lead: str, other: str) -> str:
    beat = {
        "interview": f"{lead} answers a real interview question with a confident wrong answer, then makes it worse. {other} goes still and does not laugh.",
        "traffic": f"{lead} bargains like this is philosophy. {other} names a price. {lead} tries to renegotiate the driver's mood.",
        "office": f"{lead} treats a deadline as optional. {other} does not smile.",
        "home": f"{other} asks a simple home question. {lead} answers like a lawyer, then confesses.",
        "exam": f"{lead} sounds prepared. One follow-up proves he is not.",
    }.get(situation, f"{lead} says the overconfident line. {other} punctures it. {lead} doubles down.")
    return (
        f"THIRD-PERSON two-shot. FIRST FRAME shows both faces. {beat} "
        "They talk to each other, never into a phone. No selfie arm. Same room the whole take."
    )


def comedy_script_lines(idea: str, language: str) -> List[str]:
    situation = detect_situation(idea)
    bn = str(language).startswith("bengali") or has_bengali(idea)
    lines = _pick(_BANK[(situation, "bn" if bn else "en")], idea)
    if is_lame_comedy(" ".join(lines)):
        lines = list(_BANK[("office", "bn" if bn else "en")][0])
    return lines


def joke_is_usable(lines: List[str], language: str) -> bool:
    text = " ".join(lines)
    if len(lines) < 3 or len(lines) > 6:
        return False
    if is_lame_comedy(text):
        return False
    if str(language).startswith("bengali") and not has_bengali(text):
        return False
    speakers = {_speaker_label(ln) for ln in lines if ln.strip()}
    if len(speakers) < 2:
        return False
    if lines[0].strip() == lines[-1].strip():
        return False
    return True


def _speaker_label(line: str) -> str:
    if ":" in line:
        return line.split(":", 1)[0].strip().lower()
    return ""


_BANK = {
    ("interview", "bn"): (
        [
            "ইন্টারভিউয়ার: এই রোলে আপনার strongest skill কী?",
            "ছেলে: স্যার, আমি খুব ভোরে উঠি।",
            "ইন্টারভিউয়ার: সেটা skill না।",
            "ছেলে: তাহলে LinkedIn-এ কেন লিখি?",
        ],
        [
            "ইন্টারভিউয়ার: আগের চাকরি কেন ছাড়লেন?",
            "ছেলে: স্যার, আমি grow করতে চেয়েছিলাম।",
            "ইন্টারভিউয়ার: কী grow?",
            "ছেলে: Salary-টা। বাকিটা same ছিল।",
        ],
        [
            "ইন্টারভিউয়ার: টেনশন হ্যান্ডেল করেন কীভাবে?",
            "ছেলে: আমি টেনশন করিই না।",
            "ইন্টারভিউয়ার: এই ইন্টারভিউতেও না?",
            "ছেলে: এটা টেনশন না স্যার। এটা content।",
        ],
        [
            "ইন্টারভিউয়ার: পাঁচ বছর পর নিজেকে কোথায় দেখেন?",
            "ছেলে: স্যার, আপনার চেয়ারে।",
            "ইন্টারভিউয়ার: নীরব।",
            "ছেলে: Joke। বাট চেয়ারটা খালি থাকলে আমি ready।",
        ],
        [
            "ইন্টারভিউয়ার: আপনার দুর্বলতা কী?",
            "ছেলে: স্যার, আমি খুব honest।",
            "ইন্টারভিউয়ার: এটা দুর্বলতা না।",
            "ছেলে: তাহলে এই লাইনটা মিথ্যা। দেখলেন?",
        ],
        [
            "ইন্টারভিউয়ার: আপনাকে কেন নিব?",
            "ছেলে: স্যার আরেকজন আসে নাই।",
            "ইন্টারভিউয়ার: সেটা কারণ না।",
            "ছেলে: আমি আছি। আজকের জন্য enough।",
        ],
    ),
    ("interview", "en"): (
        [
            "Interviewer: What's your strongest skill for this role?",
            "Candidate: I wake up very early.",
            "Interviewer: That is not a skill.",
            "Candidate: Then why is it on LinkedIn?",
        ],
        [
            "Interviewer: Why did you leave the last job?",
            "Candidate: I wanted to grow.",
            "Interviewer: Grow what?",
            "Candidate: The salary. Everything else was the same.",
        ],
    ),
    ("traffic", "bn"): (
        [
            "ড্রাইভার: মিটার নাকি ফিক্সড?",
            "যাত্রী: ভাই যেমন আপনার মন চায়।",
            "ড্রাইভার: তাহলে দুইশ।",
            "যাত্রী: মনটা একটু কম চায় না?",
        ],
        [
            "যাত্রী: কতক্ষণ লাগবে?",
            "ড্রাইভার: সিগন্যাল চাইলে দশ। না চাইলে ঘণ্টা।",
            "যাত্রী: আপনি তো চাইতে পারেন।",
            "ড্রাইভার: আমি চাই। সিগন্যাল চায় না।",
        ],
    ),
    ("traffic", "en"): (
        [
            "Driver: Meter or fixed?",
            "Passenger: Whatever your heart wants.",
            "Driver: Two hundred.",
            "Passenger: Can the heart want a little less?",
        ],
    ),
    ("office", "bn"): (
        [
            "বস: ডেডলাইন কাল।",
            "জুনিয়র: স্যার কালকে আমি off।",
            "বস: কাজটা?",
            "জুনিয়র: ওটাও off। সাথে নিয়ে যাব ভাবছিলাম।",
        ],
        [
            "বস: এই রিপোর্ট কোথায়?",
            "জুনিয়র: স্যার, almost done।",
            "বস: Almost কতক্ষণ?",
            "জুনিয়র: যতক্ষণ আপনি আর জিজ্ঞেস করবেন না।",
        ],
    ),
    ("office", "en"): (
        [
            "Boss: Deadline is tomorrow.",
            "Junior: I'm off tomorrow.",
            "Boss: And the work?",
            "Junior: Also off. I was going to take it with me.",
        ],
    ),
    ("home", "bn"): (
        [
            "মা: কখন আসবি?",
            "ছেলে: আজ রাত।",
            "মা: খাইয়া আসবি তো?",
            "ছেলে: না। বাসায় খেয়ে তোমাকে বলব খাইয়া আসছি।",
        ],
        [
            "মা: অফিস কেমন?",
            "ছেলে: ভালো। ব্যস্ত।",
            "মা: তাই বলে ফোন করস না?",
            "ছেলে: ব্যস্ততার প্রমাণ এটাই মা।",
        ],
    ),
    ("home", "en"): (
        [
            "Mom: When are you coming?",
            "Son: Tonight.",
            "Mom: You'll eat before you come?",
            "Son: No. I'll eat at home and tell you I already ate.",
        ],
    ),
    ("exam", "bn"): (
        [
            "বন্ধু: পড়লি?",
            "ছেলে: Concept clear।",
            "বন্ধু: চ্যাপ্টার কয়টা?",
            "ছেলে: Concept-টাই clear যে চ্যাপ্টার দেখি নাই।",
        ],
    ),
    ("exam", "en"): (
        [
            "Friend: Did you study?",
            "Student: The concept is clear.",
            "Friend: How many chapters?",
            "Student: The concept is that I did not open them.",
        ],
    ),
}


def parse_joke_lines(text: str) -> List[str]:
    lines = [re.sub(r"^[-•\d.\s]+", "", ln).strip() for ln in (text or "").splitlines()]
    return [ln for ln in lines if ln and ":" in ln]
