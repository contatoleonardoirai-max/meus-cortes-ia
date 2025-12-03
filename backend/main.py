# backend: main.py
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import tempfile
import subprocess
import uuid
from pathlib import Path
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Diretórios para armazenar vídeos e clipes
CLIPS_DIR = Path("./clips_output")
CLIPS_DIR.mkdir(exist_ok=True)

# Servir arquivos estáticos (clipes gerados)
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")


def get_video_duration(video_path: str) -> float:
    """Obtém duração do vídeo usando ffprobe"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def extract_audio(video_path: str, audio_path: str):
    """Extrai áudio do vídeo para transcrição"""
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vn",  # sem vídeo
        "-acodec", "pcm_s16le",
        "-ar", "16000",  # sample rate para Whisper
        "-ac", "1",  # mono
        "-y",
        audio_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def transcribe_audio(audio_path: str) -> list:
    """
    Transcreve áudio usando Whisper (requer: pip install openai-whisper)
    Retorna lista de segmentos com timestamps
    """
    try:
        import whisper
        model = whisper.load_model("base")  # ou "small", "medium"
        result = model.transcribe(audio_path, word_timestamps=True)
        return result["segments"]
    except ImportError:
        print("⚠️ Whisper não instalado. Legendas desabilitadas.")
        return []


def find_interesting_moments(duration: float, clips_count: int, max_duration: int, segments: list = None) -> list:
    """
    Encontra momentos interessantes no vídeo
    Se houver transcrição, prioriza segmentos com mais palavras
    """
    moments = []
    
    if segments and len(segments) > 0:
        # Estratégia 1: Segmentos com mais densidade de palavras
        scored_segments = []
        for seg in segments:
            words_count = len(seg.get("text", "").split())
            score = words_count / (seg["end"] - seg["start"])  # palavras por segundo
            scored_segments.append({
                "start": seg["start"],
                "end": seg["end"],
                "score": score
            })
        
        # Ordena por score e pega os melhores
        scored_segments.sort(key=lambda x: x["score"], reverse=True)
        
        for i, seg in enumerate(scored_segments[:clips_count]):
            start = max(0, seg["start"] - 1)  # 1s de margem antes
            end = min(duration, start + max_duration)
            
            # Ajusta se passar da duração máxima
            if end - start > max_duration:
                end = start + max_duration
            
            moments.append({"id": i + 1, "start": start, "end": end})
    else:
        # Estratégia 2: Distribuir uniformemente (fallback)
        segment_duration = duration / clips_count
        for i in range(clips_count):
            start = i * segment_duration
            end = min(start + max_duration, duration)
            moments.append({"id": i + 1, "start": start, "end": end})
    
    return moments


def create_subtitle_file(segments: list, start_time: float, end_time: float, output_path: str):
    """Cria arquivo SRT com legendas para o trecho do vídeo"""
    relevant_segments = [s for s in segments if s["start"] >= start_time and s["end"] <= end_time]
    
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(relevant_segments, 1):
            # Ajusta timestamps relativos ao início do clipe
            start = seg["start"] - start_time
            end = seg["end"] - start_time
            
            # Formato SRT: HH:MM:SS,mmm
            start_str = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d},{int((start%1)*1000):03d}"
            end_str = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d},{int((end%1)*1000):03d}"
            
            f.write(f"{i}\n")
            f.write(f"{start_str} --> {end_str}\n")
            f.write(f"{seg['text'].strip()}\n\n")


def cut_video_with_subtitles(video_path: str, start: float, end: float, output_path: str, 
                             segments: list = None, platform: str = "youtube"):
    """Corta vídeo e adiciona legendas queimadas"""
    
    # Dimensões por plataforma
    dimensions = {
        "youtube": "1920:1080",
        "instagram": "1080:1920",
        "tiktok": "1080:1920"
    }
    scale = dimensions.get(platform, "1920:1080")
    
    # Cria arquivo de legendas temporário
    subtitle_path = None
    if segments:
        subtitle_path = output_path.replace(".mp4", ".srt")
        create_subtitle_file(segments, start, end, subtitle_path)
    
    # Comando ffmpeg
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-ss", str(start),
        "-t", str(end - start),
    ]
    
    # Filtro de vídeo: escala + legendas
    vf_filters = [f"scale={scale}:force_original_aspect_ratio=decrease,pad={scale}:(ow-iw)/2:(oh-ih)/2"]
    
    if subtitle_path and os.path.exists(subtitle_path):
        # Estilo das legendas (amarelo, fundo preto semi-transparente)
        vf_filters.append(f"subtitles={subtitle_path}:force_style='FontSize=24,PrimaryColour=&H00FFFF,OutlineColour=&H40000000,BorderStyle=3'")
    
    cmd.extend([
        "-vf", ",".join(vf_filters),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        output_path
    ])
    
    subprocess.run(cmd, check=True, capture_output=True)
    
    # Remove arquivo de legendas temporário
    if subtitle_path and os.path.exists(subtitle_path):
        os.remove(subtitle_path)


def download_video_from_url(url: str, output_path: str):
    """Baixa vídeo de URL usando yt-dlp"""
    cmd = [
        "yt-dlp",
        "-f", "best[ext=mp4]",
        "-o", output_path,
        url
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@app.get("/api/health")
def health():
    return {"status": "ok", "ffmpeg": check_ffmpeg(), "whisper": check_whisper()}


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except:
        return False


def check_whisper() -> bool:
    try:
        import whisper
        return True
    except ImportError:
        return False


@app.post("/api/generate-clips-from-url")
async def generate_clips_from_url(
    videoUrl: str,
    clipsCount: int,
    maxDuration: int,
    platform: str
):
    if not check_ffmpeg():
        raise HTTPException(status_code=500, detail="FFmpeg não está instalado")
    
    # ID único para este processamento
    job_id = str(uuid.uuid4())[:8]
    
    # Diretório temporário para este job
    temp_dir = CLIPS_DIR / job_id
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # 1. Baixar vídeo
        video_path = str(temp_dir / "original.mp4")
        download_video_from_url(videoUrl, video_path)
        
        # 2. Obter duração
        duration = get_video_duration(video_path)
        
        # 3. Extrair e transcrever áudio
        segments = []
        if check_whisper():
            audio_path = str(temp_dir / "audio.wav")
            extract_audio(video_path, audio_path)
            segments = transcribe_audio(audio_path)
            os.remove(audio_path)
        
        # 4. Encontrar momentos interessantes
        moments = find_interesting_moments(duration, clipsCount, maxDuration, segments)
        
        # 5. Gerar clipes
        clips = []
        for moment in moments:
            clip_filename = f"{job_id}_clip_{moment['id']}.mp4"
            clip_path = str(CLIPS_DIR / clip_filename)
            
            cut_video_with_subtitles(
                video_path,
                moment["start"],
                moment["end"],
                clip_path,
                segments,
                platform
            )
            
            clips.append({
                "id": moment["id"],
                "start": moment["start"],
                "end": moment["end"],
                "downloadUrl": f"/clips/{clip_filename}"
            })
        
        # Limpa vídeo original
        os.remove(video_path)
        
        return {"mode": "url", "clips": clips}
    
    except Exception as e:
        # Limpa em caso de erro
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-clips-from-upload")
async def generate_clips_from_upload(
    file: UploadFile = File(...),
    clipsCount: int = Form(...),
    maxDuration: int = Form(...),
    platform: str = Form(...),
):
    if not check_ffmpeg():
        raise HTTPException(status_code=500, detail="FFmpeg não está instalado")
    
    job_id = str(uuid.uuid4())[:8]
    temp_dir = CLIPS_DIR / job_id
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # 1. Salvar upload
        video_path = str(temp_dir / "uploaded.mp4")
        with open(video_path, "wb") as f:
            f.write(await file.read())
        
        # 2. Obter duração
        duration = get_video_duration(video_path)
        
        # 3. Transcrever
        segments = []
        if check_whisper():
            audio_path = str(temp_dir / "audio.wav")
            extract_audio(video_path, audio_path)
            segments = transcribe_audio(audio_path)
            os.remove(audio_path)
        
        # 4. Encontrar momentos
        moments = find_interesting_moments(duration, int(clipsCount), int(maxDuration), segments)
        
        # 5. Gerar clipes
        clips = []
        for moment in moments:
            clip_filename = f"{job_id}_clip_{moment['id']}.mp4"
            clip_path = str(CLIPS_DIR / clip_filename)
            
            cut_video_with_subtitles(
                video_path,
                moment["start"],
                moment["end"],
                clip_path,
                segments,
                platform
            )
            
            clips.append({
                "id": moment["id"],
                "start": moment["start"],
                "end": moment["end"],
                "downloadUrl": f"/clips/{clip_filename}"
            })
        
        os.remove(video_path)
        
        return {"mode": "upload", "clips": clips}
    
    except Exception as e:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":

    uvicorn.run(app, host="0.0.0.0", port=8000)
# backend: main.py
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import tempfile
import subprocess
import uuid
from pathlib import Path
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Diretórios para armazenar vídeos e clipes
CLIPS_DIR = Path("./clips_output")
CLIPS_DIR.mkdir(exist_ok=True)

# Servir arquivos estáticos (clipes gerados)
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")


def get_video_duration(video_path: str) -> float:
    """Obtém duração do vídeo usando ffprobe"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def extract_audio(video_path: str, audio_path: str):
    """Extrai áudio do vídeo para transcrição"""
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vn",  # sem vídeo
        "-acodec", "pcm_s16le",
        "-ar", "16000",  # sample rate para Whisper
        "-ac", "1",  # mono
        "-y",
        audio_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def transcribe_audio(audio_path: str) -> list:
    """
    Transcreve áudio usando Whisper (requer: pip install openai-whisper)
    Retorna lista de segmentos com timestamps
    """
    try:
        import whisper
        model = whisper.load_model("base")  # ou "small", "medium"
        result = model.transcribe(audio_path, word_timestamps=True)
        return result["segments"]
    except ImportError:
        print("⚠️ Whisper não instalado. Legendas desabilitadas.")
        return []


def find_interesting_moments(duration: float, clips_count: int, max_duration: int, segments: list = None) -> list:
    """
    Encontra momentos interessantes no vídeo
    Se houver transcrição, prioriza segmentos com mais palavras
    """
    moments = []
    
    if segments and len(segments) > 0:
        # Estratégia 1: Segmentos com mais densidade de palavras
        scored_segments = []
        for seg in segments:
            words_count = len(seg.get("text", "").split())
            score = words_count / (seg["end"] - seg["start"])  # palavras por segundo
            scored_segments.append({
                "start": seg["start"],
                "end": seg["end"],
                "score": score
            })
        
        # Ordena por score e pega os melhores
        scored_segments.sort(key=lambda x: x["score"], reverse=True)
        
        for i, seg in enumerate(scored_segments[:clips_count]):
            start = max(0, seg["start"] - 1)  # 1s de margem antes
            end = min(duration, start + max_duration)
            
            # Ajusta se passar da duração máxima
            if end - start > max_duration:
                end = start + max_duration
            
            moments.append({"id": i + 1, "start": start, "end": end})
    else:
        # Estratégia 2: Distribuir uniformemente (fallback)
        segment_duration = duration / clips_count
        for i in range(clips_count):
            start = i * segment_duration
            end = min(start + max_duration, duration)
            moments.append({"id": i + 1, "start": start, "end": end})
    
    return moments


def create_subtitle_file(segments: list, start_time: float, end_time: float, output_path: str):
    """Cria arquivo SRT com legendas para o trecho do vídeo"""
    relevant_segments = [s for s in segments if s["start"] >= start_time and s["end"] <= end_time]
    
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(relevant_segments, 1):
            # Ajusta timestamps relativos ao início do clipe
            start = seg["start"] - start_time
            end = seg["end"] - start_time
            
            # Formato SRT: HH:MM:SS,mmm
            start_str = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d},{int((start%1)*1000):03d}"
            end_str = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d},{int((end%1)*1000):03d}"
            
            f.write(f"{i}\n")
            f.write(f"{start_str} --> {end_str}\n")
            f.write(f"{seg['text'].strip()}\n\n")


def cut_video_with_subtitles(video_path: str, start: float, end: float, output_path: str, 
                             segments: list = None, platform: str = "youtube"):
    """Corta vídeo e adiciona legendas queimadas"""
    
    # Dimensões por plataforma
    dimensions = {
        "youtube": "1920:1080",
        "instagram": "1080:1920",
        "tiktok": "1080:1920"
    }
    scale = dimensions.get(platform, "1920:1080")
    
    # Cria arquivo de legendas temporário
    subtitle_path = None
    if segments:
        subtitle_path = output_path.replace(".mp4", ".srt")
        create_subtitle_file(segments, start, end, subtitle_path)
    
    # Comando ffmpeg
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-ss", str(start),
        "-t", str(end - start),
    ]
    
    # Filtro de vídeo: escala + legendas
    vf_filters = [f"scale={scale}:force_original_aspect_ratio=decrease,pad={scale}:(ow-iw)/2:(oh-ih)/2"]
    
    if subtitle_path and os.path.exists(subtitle_path):
        # Estilo das legendas (amarelo, fundo preto semi-transparente)
        vf_filters.append(f"subtitles={subtitle_path}:force_style='FontSize=24,PrimaryColour=&H00FFFF,OutlineColour=&H40000000,BorderStyle=3'")
    
    cmd.extend([
        "-vf", ",".join(vf_filters),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        output_path
    ])
    
    subprocess.run(cmd, check=True, capture_output=True)
    
    # Remove arquivo de legendas temporário
    if subtitle_path and os.path.exists(subtitle_path):
        os.remove(subtitle_path)


def download_video_from_url(url: str, output_path: str):
    """Baixa vídeo de URL usando yt-dlp"""
    cmd = [
        "yt-dlp",
        "-f", "best[ext=mp4]",
        "-o", output_path,
        url
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@app.get("/api/health")
def health():
    return {"status": "ok", "ffmpeg": check_ffmpeg(), "whisper": check_whisper()}


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except:
        return False


def check_whisper() -> bool:
    try:
        import whisper
        return True
    except ImportError:
        return False


@app.post("/api/generate-clips-from-url")
async def generate_clips_from_url(
    videoUrl: str,
    clipsCount: int,
    maxDuration: int,
    platform: str
):
    if not check_ffmpeg():
        raise HTTPException(status_code=500, detail="FFmpeg não está instalado")
    
    # ID único para este processamento
    job_id = str(uuid.uuid4())[:8]
    
    # Diretório temporário para este job
    temp_dir = CLIPS_DIR / job_id
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # 1. Baixar vídeo
        video_path = str(temp_dir / "original.mp4")
        download_video_from_url(videoUrl, video_path)
        
        # 2. Obter duração
        duration = get_video_duration(video_path)
        
        # 3. Extrair e transcrever áudio
        segments = []
        if check_whisper():
            audio_path = str(temp_dir / "audio.wav")
            extract_audio(video_path, audio_path)
            segments = transcribe_audio(audio_path)
            os.remove(audio_path)
        
        # 4. Encontrar momentos interessantes
        moments = find_interesting_moments(duration, clipsCount, maxDuration, segments)
        
        # 5. Gerar clipes
        clips = []
        for moment in moments:
            clip_filename = f"{job_id}_clip_{moment['id']}.mp4"
            clip_path = str(CLIPS_DIR / clip_filename)
            
            cut_video_with_subtitles(
                video_path,
                moment["start"],
                moment["end"],
                clip_path,
                segments,
                platform
            )
            
            clips.append({
                "id": moment["id"],
                "start": moment["start"],
                "end": moment["end"],
                "downloadUrl": f"/clips/{clip_filename}"
            })
        
        # Limpa vídeo original
        os.remove(video_path)
        
        return {"mode": "url", "clips": clips}
    
    except Exception as e:
        # Limpa em caso de erro
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-clips-from-upload")
async def generate_clips_from_upload(
    file: UploadFile = File(...),
    clipsCount: int = Form(...),
    maxDuration: int = Form(...),
    platform: str = Form(...),
):
    if not check_ffmpeg():
        raise HTTPException(status_code=500, detail="FFmpeg não está instalado")
    
    job_id = str(uuid.uuid4())[:8]
    temp_dir = CLIPS_DIR / job_id
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # 1. Salvar upload
        video_path = str(temp_dir / "uploaded.mp4")
        with open(video_path, "wb") as f:
            f.write(await file.read())
        
        # 2. Obter duração
        duration = get_video_duration(video_path)
        
        # 3. Transcrever
        segments = []
        if check_whisper():
            audio_path = str(temp_dir / "audio.wav")
            extract_audio(video_path, audio_path)
            segments = transcribe_audio(audio_path)
            os.remove(audio_path)
        
        # 4. Encontrar momentos
        moments = find_interesting_moments(duration, int(clipsCount), int(maxDuration), segments)
        
        # 5. Gerar clipes
        clips = []
        for moment in moments:
            clip_filename = f"{job_id}_clip_{moment['id']}.mp4"
            clip_path = str(CLIPS_DIR / clip_filename)
            
            cut_video_with_subtitles(
                video_path,
                moment["start"],
                moment["end"],
                clip_path,
                segments,
                platform
            )
            
            clips.append({
                "id": moment["id"],
                "start": moment["start"],
                "end": moment["end"],
                "downloadUrl": f"/clips/{clip_filename}"
            })
        
        os.remove(video_path)
        
        return {"mode": "upload", "clips": clips}
    
    except Exception as e:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0",port=8000,reload=True )

