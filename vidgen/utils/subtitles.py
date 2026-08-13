# Legacy subtitles helper — SubtitleAgent in agents.py is used instead
from typing import List
from vidgen.models import Scene

def format_time(s: float) -> str:
    ms = int((s % 1)*1000); m,sec = divmod(int(s),60); h,m = divmod(m,60)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"

def generate_srt(scenes: List[Scene], total_duration: float) -> str:
    lines, idx, t = [], 1, 0.0
    for sc in scenes:
        dur = sum(sh.duration for sh in sc.shots)
        if sc.narration_text:
            lines += [str(idx), f"{format_time(t)} --> {format_time(t+dur)}",
                      sc.narration_text.strip(), ""]
            idx += 1
        t += dur
    return "
".join(lines)
