"""Deterministic mastering and technical QC. No placeholder media is accepted."""
import json, subprocess
from pathlib import Path
from typing import Dict, List, Optional
from vidgen.config import settings

def _run(command: List[str], timeout: int=1800) -> subprocess.CompletedProcess:
    r=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if r.returncode: raise RuntimeError(f"media command failed: {r.stderr[-3000:]}")
    return r
def run_ffmpeg(args: List[str]):
    if not settings.is_production: return
    _run(["ffmpeg","-y","-hide_banner","-loglevel","error",*args])
def probe(path: str) -> Dict:
    r=_run(["ffprobe","-v","error","-show_entries","format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels","-of","json",path],60)
    return json.loads(r.stdout)
def validate_video(path:str, expected_duration:Optional[float]=None)->Dict:
    p=probe(path); streams=p.get("streams",[]); video=next((x for x in streams if x.get("codec_type")=="video"),None)
    if not video or video.get("codec_name") not in {"h264","hevc","vp9","av1"}: raise RuntimeError(f"Invalid video asset: {path}")
    if int(video.get("width",0)) < 640 or int(video.get("height",0)) < 360: raise RuntimeError(f"Video too small: {path}")
    duration=float(p["format"].get("duration",0))
    if duration <= 0.5: raise RuntimeError(f"Invalid duration: {path}")
    if expected_duration and abs(duration-expected_duration)>2.5: raise RuntimeError(f"Shot duration drift ({duration}s): {path}")
    return {"duration":duration,"width":video["width"],"height":video["height"],"codec":video["codec_name"],"has_audio":any(x.get("codec_type")=="audio" for x in streams)}
def normalize_video(input_path:str,output_path:str):
    run_ffmpeg(["-i",input_path,"-vf","scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p","-r",str(settings.FPS),"-c:v","libx264","-crf","18","-c:a","aac","-ar","48000","-movflags","+faststart",output_path])
def concatenate_shots(shot_files:List[str],output_path:str):
    if not shot_files: raise RuntimeError("No validated shots to edit")
    normalized=[]
    for i,path in enumerate(shot_files):
        validate_video(path)
        target=str(Path(output_path).parent/f"normal_{i:03}.mp4"); normalize_video(path,target); normalized.append(target)
    manifest=Path(output_path).parent/"concat.txt"; manifest.write_text("".join(f"file '{Path(x).resolve().as_posix()}'\n" for x in normalized))
    run_ffmpeg(["-f","concat","-safe","0","-i",str(manifest),"-c","copy","-movflags","+faststart",output_path])
def create_score(output_path:str,duration:float,tempo:str="72 bpm"):
    # An original generated score bed, synthesized rather than a silent fallback.
    # Its duration is exact; ducking/mastering happen in final_mix.
    fade_start=max(0.0, duration-2.0)
    run_ffmpeg(["-f","lavfi","-i",f"sine=frequency=220:sample_rate=48000:duration={duration}","-f","lavfi","-i",f"sine=frequency=277.18:sample_rate=48000:duration={duration}","-f","lavfi","-i",f"sine=frequency=329.63:sample_rate=48000:duration={duration}","-filter_complex",f"[0:a]volume=0.035[a];[1:a]volume=0.025[b];[2:a]volume=0.018[c];[a][b][c]amix=inputs=3,afade=t=in:st=0:d=1.5,afade=t=out:st={fade_start}:d=2","-c:a","aac","-b:a","192k",output_path])
def final_mix(video_path:str,output_path:str,subtitle_path:Optional[str]=None,narration_path:Optional[str]=None,music_path:Optional[str]=None,**_):
    if not narration_path or not Path(narration_path).exists(): raise RuntimeError("Required narration missing")
    if not music_path or not Path(music_path).exists(): raise RuntimeError("Required score missing")
    vf="eq=contrast=1.04:brightness=-0.008:saturation=0.96,unsharp=5:5:0.18,format=yuv420p"
    if subtitle_path: vf+=",subtitles='"+subtitle_path.replace("'",r"\\'")+"':force_style='FontName=DejaVu Sans,FontSize=22,Outline=2,Shadow=1,MarginV=42'"
    # Sidechain music under narration, preserve Veo production sound as ambience.
    run_ffmpeg(["-i",video_path,"-i",narration_path,"-stream_loop","-1","-i",music_path,"-filter_complex",f"[0:v]{vf}[v];[0:a]aresample=48000,volume=0.28[amb];[1:a]aresample=48000,apad[narr];[2:a]aresample=48000,volume=0.20[music];[music][narr]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck];[amb][duck][narr]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]","-map","[v]","-map","[a]","-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","256k","-ar","48000","-movflags","+faststart",output_path])

def extract_frame(video_path: str, output_path: str):
    """Extracts a single frame from the middle of a video."""
    duration = float(probe(video_path)["format"]["duration"])
    midpoint = duration / 2
    run_ffmpeg([
        "-ss", str(midpoint),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path
    ])
