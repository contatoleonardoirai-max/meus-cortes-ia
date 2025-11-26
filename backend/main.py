import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from moviepy.editor import VideoFileClip
from yt_dlp import YoutubeDL


# ==== Config geral ====

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
CLIPS_DIR = BASE_DIR / "clips"

DOWNLOADS_DIR.mkdir(exist_ok=True)
CLIPS_DIR.mkdir(exist_ok=True)


class GenerateRequest(BaseModel):
    videoUrl: str
    clipsCount: int
    maxDuration: int
    platform: str


app = FastAPI()

# CORS liberado para desenvolvimento (front no Live Server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir os cortes prontos em /clips/...
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "env": "dev",
        "message": "Backend rodando com corte REAL (link e upload)",
    }


# ==== Funções utilitárias ====


def download_video(url: str) -> Path:
    """Baixa o vídeo a partir de uma URL usando yt-dlp."""
    video_id = str(uuid.uuid4())
    output_template = str(DOWNLOADS_DIR / f"{video_id}.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "format": "mp4/bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        ext = info.get("ext", "mp4")
        file_path = DOWNLOADS_DIR / f"{video_id}.{ext}"

    if not file_path.exists():
        raise RuntimeError("Falha ao baixar o vídeo.")

    return file_path


def generate_real_clips(
    video_path: Path,
    clips_count: int,
    max_duration: int,
    platform: str,
):
    """Abre o vídeo e gera cortes REAIS no disco, retornando metadados."""
    clips_meta = []

    with VideoFileClip(str(video_path)) as video:
        duration = video.duration  # segundos
        fps = video.fps or 30

        if clips_count > 20:
            clips_count = 20

        if duration <= 0:
            raise RuntimeError("Duração do vídeo inválida.")

        step = max_duration

        for i in range(clips_count):
            start = i * step
            if start >= duration:
                break

            end = min(start + step, duration)

            clip_filename = f"clip_{uuid.uuid4().hex[:8]}_{i+1}.mp4"
            clip_path = CLIPS_DIR / clip_filename

            subclip = video.subclip(start, end)
            subclip.write_videofile(
                str(clip_path),
                codec="libx264",
                audio_codec="aac",
                fps=fps,
                verbose=False,
                logger=None,
            )

            clips_meta.append(
                {
                    "id": i + 1,
                    "start": int(start),
                    "end": int(end),
                    "platform": platform,
                    "downloadUrl": f"/clips/{clip_filename}",
                }
            )

    return clips_meta


# ==== Endpoint por LINK ====


@app.post("/api/generate-clips-from-url")
def generate_clips_from_url(payload: GenerateRequest):
    video_url = payload.videoUrl.strip()
    clips_count = payload.clipsCount
    max_duration = payload.maxDuration
    platform = (payload.platform or "shorts").strip() or "shorts"

    if not video_url or not video_url.startswith("http"):
        raise HTTPException(
            status_code=400,
            detail="Informe um link de vídeo válido (começando com http).",
        )

    if clips_count <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantidade de cortes deve ser maior que 0.",
        )

    if max_duration < 5:
        raise HTTPException(
            status_code=400,
            detail="Duração máxima deve ser pelo menos 5 segundos.",
        )

    try:
        video_path = download_video(video_url)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao baixar o vídeo: {e}",
        )

    try:
        clips = generate_real_clips(
            video_path=video_path,
            clips_count=clips_count,
            max_duration=max_duration,
            platform=platform,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao gerar cortes: {e}",
        )
    finally:
        if video_path.exists():
            try:
                os.remove(video_path)
            except OSError:
                pass

    if not clips:
        raise HTTPException(
            status_code=400,
            detail="Nenhum corte pôde ser gerado. Verifique o vídeo/parâmetros.",
        )

    return {
        "mode": "url",
        "videoUrl": video_url,
        "clipsCount": len(clips),
        "maxDuration": max_duration,
        "platform": platform,
        "clips": clips,
    }


# ==== Endpoint por UPLOAD ====


@app.post("/api/generate-clips-from-upload")
async def generate_clips_from_upload(
    file: UploadFile = File(...),
    clipsCount: int = Form(...),
    maxDuration: int = Form(...),
    platform: str = Form("shorts"),
):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Nenhum arquivo de vídeo enviado.",
        )

    if clipsCount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantidade de cortes deve ser maior que 0.",
        )

    if maxDuration < 5:
        raise HTTPException(
            status_code=400,
            detail="Duração máxima deve ser pelo menos 5 segundos.",
        )

    suffix = Path(file.filename).suffix or ".mp4"
    video_id = uuid.uuid4().hex
    video_path = DOWNLOADS_DIR / f"upload_{video_id}{suffix}"

    with open(video_path, "wb") as f:
        f.write(await file.read())

    platform = (platform or "shorts").strip() or "shorts"

    try:
        clips = generate_real_clips(
            video_path=video_path,
            clips_count=clipsCount,
            max_duration=maxDuration,
            platform=platform,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao gerar cortes a partir do upload: {e}",
        )
    finally:
        if video_path.exists():
            try:
                os.remove(video_path)
            except OSError:
                pass

    if not clips:
        raise HTTPException(
            status_code=400,
            detail="Nenhum corte pôde ser gerado a partir do upload.",
        )

    return {
        "mode": "upload",
        "filename": file.filename,
        "clipsCount": len(clips),
        "maxDuration": maxDuration,
        "platform": platform,
        "clips": clips,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )

