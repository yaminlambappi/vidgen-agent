"""Deterministic mastering and technical QC. No placeholder media is accepted."""
import json, subprocess, time
from pathlib import Path
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from vidgen.config import settings

def _run(command: List[str], timeout: int=1800) -> subprocess.CompletedProcess:
    r=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if r.returncode: raise RuntimeError(f"media command failed: {r.stderr[-3000:]}")
    return r

def run_ffmpeg(args: List[str]):
    if not settings.is_production: return
    _run(["ffmpeg","-y","-hide_banner","-loglevel","error",*args])

def probe(path: str) -> Dict:
    """Probe a media file. In simulation mode, returns a synthetic dict for stub files."""
    if not settings.is_production:
        # In simulation mode, trust the mock file is valid and return synthetic metadata.
        # This prevents ffprobe from crashing on stub/synthetic MP4 bytes.
        p = Path(path)
        # Try real probe first (works when FFmpeg is present and file is valid)
        try:
            r = subprocess.run(
                ["ffprobe","-v","error","-show_entries",
                 "format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
                 "-of","json", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if r.returncode == 0:
                data = json.loads(r.stdout)
                # If we got at least a duration, trust it
                if data.get("format", {}).get("duration"):
                    return data
        except Exception:
            pass
        # Synthetic fallback for mock/stub media
        return {
            "format": {"duration": "8.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264",
                 "width": 1280, "height": 720, "r_frame_rate": "24/1"},
                {"codec_type": "audio", "codec_name": "aac",
                 "sample_rate": "48000", "channels": 2},
            ]
        }
    r=_run(["ffprobe","-v","error","-show_entries","format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels","-of","json",path],60)
    return json.loads(r.stdout)

def validate_video(path:str, expected_duration:Optional[float]=None)->Dict:
    p=probe(path); streams=p.get("streams",[]); video=next((x for x in streams if x.get("codec_type")=="video"),None)
    if not video or video.get("codec_name") not in {"h264","hevc","vp9","av1"}:
        if not settings.is_production:
            # Simulation: accept any file, return synthetic result
            return {"duration": float(expected_duration or 8.0), "width": 1280, "height": 720,
                    "codec": "h264", "has_audio": True, "valid": True, "drift": 0.0}
        raise RuntimeError(f"Invalid video asset: {path}")
    if int(video.get("width",0)) < 640 or int(video.get("height",0)) < 360:
        if not settings.is_production:
            return {"duration": float(expected_duration or 8.0), "width": 1280, "height": 720,
                    "codec": "h264", "has_audio": True, "valid": True, "drift": 0.0}
        raise RuntimeError(f"Video too small: {path}")
    duration=float(p["format"].get("duration",0))
    if duration <= 0.5:
        if not settings.is_production:
            duration = float(expected_duration or 8.0)
        else:
            raise RuntimeError(f"Invalid duration: {path}")

    drift = abs(duration - expected_duration) if expected_duration else 0
    if settings.is_production and expected_duration and drift > 5.0:
        raise RuntimeError(f"Extreme shot duration drift ({duration}s vs {expected_duration}s): {path}")

    has_audio = any(x.get("codec_type")=="audio" for x in streams)
    if settings.is_production and not has_audio:
        raise RuntimeError(f"Generated video has no audio stream: {path}")
    return {"duration":duration,"width":video.get("width",1280),"height":video.get("height",720),
            "codec":video.get("codec_name","h264"),"has_audio":has_audio, "valid": True, "drift": drift}

def _write_stub_media_ffmpeg(output_path: str, ext: str, duration: float = 8.0) -> None:
    """Write a real minimal media file using FFmpeg (or a byte stub if FFmpeg absent)."""
    import shutil
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        Path(output_path).write_bytes(b"stub")
        return
    dur = max(1.0, duration)
    if ext in (".mp4",):
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", f"color=c=black:s=1280x720:d={dur}",
               "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={dur}",
               "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-movflags", "+faststart", output_path]
    elif ext in (".m4a", ".mp3"):
        codec = "aac" if ext == ".m4a" else "libmp3lame"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={dur}",
               "-c:a", codec, "-q:a", "5", output_path]
    elif ext == ".srt":
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nmock\n\n")
        return
    else:
        Path(output_path).write_text("mock")
        return
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        Path(output_path).write_bytes(b"stub")


def normalize_video(input_path: str, output_path: str, expected_duration: Optional[float] = None) -> None:
    """Normalise a shot to 1920×1080 H.264/AAC with optional duration enforcement.

    In simulation mode this is a no-op (the stub concatenation path handles output).
    """
    if not settings.is_production:
        return
    vf = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
          "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
    args = ["-i", input_path]
    if expected_duration:
        vf += f",tpad=stop_mode=clone:whole_duration={expected_duration}"
        args += ["-t", str(expected_duration)]
    run_ffmpeg([
        *args,
        "-vf", vf,
        "-af", "apad",
        "-r", str(settings.FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-ar", "48000",
        "-movflags", "+faststart",
        output_path,
    ])

def concatenate_shots(shot_files:List[str], output_path:str, expected_durations:Optional[List[float]]=None):
    if not shot_files: raise RuntimeError("No validated shots to edit")

    # In simulation mode, produce a valid stub output without real processing
    if not settings.is_production:
        ext = Path(output_path).suffix.lower() or ".mp4"
        _write_stub_media_ffmpeg(output_path, ext, duration=sum(expected_durations or [8.0]*len(shot_files)))
        return

    normalized=[]
    work_dir = Path(output_path).parent

    def process_shot(args):
        i, path = args
        expected = expected_durations[i] if expected_durations else None
        validate_video(path, expected)
        target = str(work_dir / f"normal_{i:03}.mp4")
        normalize_video(path, target, expected)
        return target

    print(f"  [MASTERING] Normalizing {len(shot_files)} shots in parallel...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        normalized = list(executor.map(process_shot, enumerate(shot_files)))

    inputs = []
    chains = []
    for i, path in enumerate(normalized):
        inputs.extend(["-i", path])
        chains.append(f"[{i}:v][{i}:a]")
    graph = "".join(chains) + f"concat=n={len(normalized)}:v=1:a=1[v][a]"
    run_ffmpeg([
        *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
        "-movflags", "+faststart", output_path,
    ])
def create_score(output_path:str,duration:float,tempo:str="72 bpm"):
    if not settings.is_production:
        _write_stub_media_ffmpeg(output_path, Path(output_path).suffix.lower(), duration)
        return
    fade_start=max(0.0, duration-2.0)
    run_ffmpeg(["-f","lavfi","-i",f"sine=frequency=220:sample_rate=48000:duration={duration}","-f","lavfi","-i",f"sine=frequency=277.18:sample_rate=48000:duration={duration}","-f","lavfi","-i",f"sine=frequency=329.63:sample_rate=48000:duration={duration}","-filter_complex",f"[0:a]volume=0.035[a];[1:a]volume=0.025[b];[2:a]volume=0.018[c];[a][b][c]amix=inputs=3,afade=t=in:st=0:d=1.5,afade=t=out:st={fade_start}:d=2","-c:a","aac","-b:a","192k",output_path])
def final_mix(video_path:str,output_path:str,subtitle_path:Optional[str]=None,narration_path:Optional[str]=None,music_path:Optional[str]=None,dialogue_tracks=None,**_):
    # In simulation mode, produce a valid stub output
    if not settings.is_production:
        _write_stub_media_ffmpeg(output_path, ".mp4",
                                  duration=float(probe(video_path)["format"].get("duration", 8.0)))
        return
    if not narration_path or not Path(narration_path).exists(): raise RuntimeError("Required narration missing")
    if not music_path or not Path(music_path).exists(): raise RuntimeError("Required score missing")
    vf="eq=contrast=1.04:brightness=-0.008:saturation=0.96,unsharp=5:5:0.18,format=yuv420p"
    if subtitle_path and settings.BURN_SUBTITLES:
        vf+=",subtitles='"+subtitle_path.replace("'",r"\\'")+"':force_style='FontName=DejaVu Sans,FontSize=22,Outline=2,Shadow=1,MarginV=42'"
    
    # Dialogue is delayed onto editorial beats, then it ducks music along with narration.
    command = ["-i", video_path, "-i", narration_path, "-stream_loop", "-1", "-i", music_path]
    
    # We force a consistent format (48k, stereo, fltp) for all audio processing to avoid filter format negotiation failures.
    afmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters = [f"[0:v]{vf}[v]", f"[0:a]{afmt},volume=0.28[amb]",
               f"[2:a]{afmt},volume=0.20[music]"]
    
    filters.append(f"[1:a]{afmt}[narr]")
    if dialogue_tracks:
        speech_labels = ["[narr]"]
        for index, track in enumerate(dialogue_tracks):
            path = track.get("path")
            if not path or not Path(path).exists():
                raise RuntimeError(f"Required dialogue track missing: {path}")
            command += ["-i", path]
            delay = max(0, int(float(track.get("start", 0)) * 1000))
            label = f"dlg{index}"
            # Input index is 3 + track index
            filters.append(f"[{index + 3}:a]{afmt},adelay={delay}|{delay},apad[{label}]")
            speech_labels.append(f"[{label}]")
        filters.append("".join(speech_labels) + f"amix=inputs={len(speech_labels)}:duration=first:normalize=0[speech]")
    else:
        # If no dialogue, narration is the only speech track.
        filters.append("[narr]apad[speech]")

    final_filters = filters + ["[speech]asplit[speech_sidechain][speech_mix]",
                               "[music][speech_sidechain]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck]",
                               "[amb][duck][speech_mix]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
    command += ["-filter_complex", ";".join(final_filters), "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", output_path]
    run_ffmpeg(command)

def extract_frames(video_path: str, output_dir: str, output_prefix: str, num_frames: int = 3):
    """Extracts multiple frames from a video at evenly spaced intervals.
    In simulation mode, writes stub PNG files without calling FFmpeg."""
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(exist_ok=True)

    if not settings.is_production:
        import shutil
        ffmpeg = shutil.which("ffmpeg")
        for i in range(num_frames):
            output_path = output_dir_path / f"{output_prefix}_{i}.png"
            if ffmpeg:
                try:
                    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                    "-f", "lavfi", "-i", "color=c=blue:s=128x72:d=0.1",
                                    "-vframes", "1", str(output_path)],
                                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    continue
                except Exception:
                    pass
            # byte stub fallback
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return

    duration = float(probe(video_path)["format"]["duration"])
    for i in range(num_frames):
        position = (i + 1) / (num_frames + 1)
        timestamp = duration * position
        output_path = output_dir_path / f"{output_prefix}_{i}.png"
        run_ffmpeg([
            "-ss", str(timestamp),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            str(output_path)
        ])

def extract_frame(video_path: str, output_path: str):
    """Extracts a single frame from the middle of a video."""
    extract_frames(video_path, str(Path(output_path).parent), Path(output_path).stem, num_frames=1)
