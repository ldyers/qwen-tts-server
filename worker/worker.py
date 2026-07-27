"""
Qwen3-TTS GPU Worker Server
============================
部署在 Windows GPU 机器上，负责加载模型和推理。
VPS 上的业务层通过 WireGuard 隧道 HTTP 调用本服务。

启动方式:
    python -m uvicorn worker:app --host 0.0.0.0 --port 8001

或者直接:
    python worker.py
"""
import io
import os
import logging
import time
import base64
import json
import asyncio
from typing import Optional, List, Dict, Any, Union
from contextlib import asynccontextmanager

import numpy as np
import soundfile as sf
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# ─── Configuration ──────────────────────────────────────────────

# 模型配置（可通过环境变量覆盖）
CUSTOM_VOICE_MODEL = os.getenv("QWEN_TTS_CUSTOM_VOICE_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
VOICE_DESIGN_MODEL  = os.getenv("QWEN_TTS_VOICE_DESIGN_MODEL",  "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
BASE_MODEL          = os.getenv("QWEN_TTS_BASE_MODEL",           "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
TOKENIZER           = os.getenv("QWEN_TTS_TOKENIZER",            "Qwen/Qwen3-TTS-Tokenizer-12Hz")

CUDA_DEVICE    = os.getenv("CUDA_DEVICE", "cuda:0")
MODEL_DTYPE    = os.getenv("MODEL_DTYPE", "bfloat16")
USE_FLASH_ATTN = os.getenv("USE_FLASH_ATTENTION", "true").lower() == "true"

# Worker 配置
WORKER_PORT    = int(os.getenv("WORKER_PORT", "8001"))
WORKER_HOST    = os.getenv("WORKER_HOST", "0.0.0.0")
PRELOAD_MODELS = os.getenv("PRELOAD_MODELS", "false").lower() == "true"
ENABLE_WARMUP  = os.getenv("ENABLE_WARMUP", "true").lower() == "true"

# 安全：Worker 间共享密钥（VPS 和 Windows 之间）
WORKER_API_KEY = os.getenv("WORKER_API_KEY", "")  # 空则不鉴权（仅 WG 内网使用）

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Model Loading ──────────────────────────────────────────────

_models: Dict[str, Any] = {
    "custom_voice": None,
    "voice_design": None,
    "base": None,
}

_model_configs = {
    "custom_voice": {"model_path": CUSTOM_VOICE_MODEL, "name": "CustomVoice"},
    "voice_design": {"model_path": VOICE_DESIGN_MODEL,  "name": "VoiceDesign"},
    "base":         {"model_path": BASE_MODEL,          "name": "Base"},
}


def _get_torch_dtype():
    import torch
    dtype_map = {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
        "float32": torch.float32,
    }
    return dtype_map.get(MODEL_DTYPE.lower(), torch.bfloat16)


def _load_model(model_type: str):
    """加载单个 TTS 模型"""
    from qwen_tts import Qwen3TTSModel

    config = _model_configs[model_type]
    model_path = config["model_path"]

    logger.info(f"Loading {config['name']} from {model_path} on {CUDA_DEVICE}")

    load_kwargs = {
        "device_map": CUDA_DEVICE,
        "dtype": _get_torch_dtype(),
    }

    if USE_FLASH_ATTN and CUDA_DEVICE != "cpu":
        try:
            import flash_attn  # noqa: F401
            load_kwargs["attn_implementation"] = "flash_attention_2"
            logger.info("Using Flash Attention 2")
        except ImportError:
            logger.warning("Flash Attention not installed, using default attention")

    try:
        model = Qwen3TTSModel.from_pretrained(model_path, **load_kwargs)
        logger.info(f"✓ {config['name']} loaded successfully")
        return model
    except Exception as e:
        logger.error(f"✗ Failed to load {config['name']}: {e}")
        raise


def get_model(model_type: str):
    """懒加载模型"""
    if _models[model_type] is not None:
        return _models[model_type]
    _models[model_type] = _load_model(model_type)
    return _models[model_type]


def is_loaded(model_type: str) -> bool:
    return _models.get(model_type) is not None


# ─── Schemas ────────────────────────────────────────────────────

class GenerateCustomVoiceRequest(BaseModel):
    text: Union[str, List[str]]
    language: Union[str, List[str]]
    speaker: Union[str, List[str]]
    instruct: Union[str, List[str]] = ""


class GenerateVoiceDesignRequest(BaseModel):
    text: Union[str, List[str]]
    language: Union[str, List[str]]
    instruct: Union[str, List[str]]


class CreateVoiceClonePromptRequest(BaseModel):
    ref_audio_base64: str  # WAV base64
    ref_text: Optional[str] = None
    x_vector_only_mode: bool = False


class GenerateVoiceCloneRequest(BaseModel):
    text: str
    language: str
    # voice_clone_prompt 是模型返回的对象，通过 pickle 序列化后 base64 传输
    voice_clone_prompt_b64: str


# ─── Audio Helpers ──────────────────────────────────────────────

def numpy_to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    buf.seek(0)
    return buf.read()


def wav_bytes_to_numpy(wav_bytes: bytes):
    audio, sr = sf.read(io.BytesIO(wav_bytes))
    return audio, sr


def numpy_to_base64(audio: np.ndarray, sr: int) -> str:
    return base64.b64encode(numpy_to_wav_bytes(audio, sr)).decode()


def serialize_prompt(prompt) -> str:
    """将 voice_clone_prompt 序列化为 base64 字符串（使用 pickle）"""
    import pickle
    return base64.b64encode(pickle.dumps(prompt)).decode()


def deserialize_prompt(prompt_b64: str):
    """从 base64 字符串反序列化 voice_clone_prompt"""
    import pickle
    return pickle.loads(base64.b64decode(prompt_b64))


# ─── Auth ───────────────────────────────────────────────────────

def check_auth(api_key: Optional[str] = None):
    """简单的 API Key 鉴权（通过 Header X-Worker-Key）"""
    if WORKER_API_KEY:
        from fastapi import Header
        # 实际鉴权在路由层通过 Depends 实现
    # 如果没设置 WORKER_API_KEY 则跳过
    return True


# ─── FastAPI App ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info(f"Qwen3-TTS GPU Worker starting on {WORKER_HOST}:{WORKER_PORT}")
    logger.info(f"CUDA Device: {CUDA_DEVICE}, Dtype: {MODEL_DTYPE}")
    logger.info(f"Flash Attention: {'enabled' if USE_FLASH_ATTN else 'disabled'}")
    logger.info("=" * 60)

    if PRELOAD_MODELS:
        logger.info("Preloading all models...")
        for mt in _models:
            try:
                get_model(mt)
            except Exception as e:
                logger.error(f"Failed to preload {mt}: {e}")

        if ENABLE_WARMUP:
            await warmup()
    else:
        logger.info("Models will be loaded on first request (lazy loading)")

    yield
    logger.info("Worker shutting down")


async def warmup():
    """预热模型"""
    logger.info("Running warmup...")
    warmup_text = "This is a warmup test to initialize the model."

    try:
        if is_loaded("custom_voice"):
            logger.info("Warming up CustomVoice...")
            m = get_model("custom_voice")
            _ = m.generate_custom_voice(text=warmup_text, language="Auto", speaker="Ryan", instruct="")
            logger.info("✓ CustomVoice warmed up")

        if is_loaded("voice_design"):
            logger.info("Warming up VoiceDesign...")
            m = get_model("voice_design")
            _ = m.generate_voice_design(text=warmup_text, language="Auto", instruct="A clear professional voice")
            logger.info("✓ VoiceDesign warmed up")

        if is_loaded("base"):
            logger.info("Warming up Base model...")
            m = get_model("base")
            sr = 24000
            t = np.linspace(0, 1.0, int(sr * 1.0))
            dummy = np.sin(2 * np.pi * 440 * t).astype(np.float32)
            prompt = m.create_voice_clone_prompt(
                ref_audio=(dummy, sr), ref_text=warmup_text, x_vector_only_mode=False
            )
            _ = m.generate_voice_clone(text=warmup_text, language="Auto", voice_clone_prompt=prompt)
            logger.info("✓ Base model warmed up")

        logger.info("Warmup complete")
    except Exception as e:
        logger.warning(f"Warmup failed (non-critical): {e}")


app = FastAPI(
    title="Qwen3-TTS GPU Worker",
    description="GPU inference worker for Qwen3-TTS. Deploy on GPU machine.",
    version="1.0.0",
    lifespan=lifespan,
)


# 鉴权中间件
@app.middleware("http")
async def auth_middleware(request, call_next):
    if WORKER_API_KEY:
        key = request.headers.get("X-Worker-Key")
        if key != WORKER_API_KEY:
            return Response(
                content=json.dumps({"detail": "Unauthorized"}),
                status_code=401,
                media_type="application/json",
            )
    return await call_next(request)


# ─── Health Endpoints ───────────────────────────────────────────

@app.get("/health")
async def health():
    import torch
    gpu_info = {}
    if torch.cuda.is_available():
        gpu_info = {
            "gpu_name": torch.cuda.get_device_name(0),
            "gpu_memory_allocated_mb": round(torch.cuda.memory_allocated(0) / 1024 / 1024, 1),
            "gpu_memory_total_mb": round(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024, 1),
        }
    return {
        "status": "healthy",
        "models_loaded": {
            "custom_voice": is_loaded("custom_voice"),
            "voice_design": is_loaded("voice_design"),
            "base": is_loaded("base"),
        },
        "gpu": gpu_info,
    }


@app.get("/health/models")
async def health_models():
    return {
        "custom_voice_loaded": is_loaded("custom_voice"),
        "voice_design_loaded": is_loaded("voice_design"),
        "base_loaded": is_loaded("base"),
        "tokenizer_loaded": True,  # tokenizer 随模型加载
    }


# ─── CustomVoice Endpoints ──────────────────────────────────────

@app.post("/generate/custom-voice")
async def generate_custom_voice(req: GenerateCustomVoiceRequest):
    """生成 CustomVoice 语音，返回 WAV bytes"""
    t0 = time.time()
    try:
        model = get_model("custom_voice")
        wavs, sr = model.generate_custom_voice(
            text=req.text,
            language=req.language,
            speaker=req.speaker,
            instruct=req.instruct if req.instruct else "",
        )
        # 保护：截断异常长音频（超过 30 秒）
        max_samples = sr * 30
        if len(wavs[0]) > max_samples:
            wavs = [wavs[0][:max_samples]]

        gen_time = time.time() - t0
        logger.info(f"CustomVoice generated in {gen_time:.2f}s, sr={sr}")

        # 如果是批量请求，返回第一个（VPS 侧处理批量逻辑）
        audio_data = wavs[0] if isinstance(wavs, list) else wavs

        wav_bytes = numpy_to_wav_bytes(audio_data, sr)
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "X-Sample-Rate": str(sr),
                "X-Generation-Time": f"{gen_time:.3f}",
                "X-Audio-Duration": f"{len(audio_data) / sr:.3f}",
            },
        )
    except Exception as e:
        logger.error(f"CustomVoice generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate/custom-voice/batch")
async def generate_custom_voice_batch(req: GenerateCustomVoiceRequest):
    """批量生成 CustomVoice，返回 JSON (base64 数组)"""
    t0 = time.time()
    try:
        model = get_model("custom_voice")
        wavs, sr = model.generate_custom_voice(
            text=req.text,
            language=req.language,
            speaker=req.speaker,
            instruct=req.instruct if req.instruct else "",
        )
        # 保护：截断异常长音频（超过 30 秒）
        max_samples = sr * 30
        if len(wavs[0]) > max_samples:
            wavs = [wavs[0][:max_samples]]

        gen_time = time.time() - t0
        logger.info(f"CustomVoice batch generated {len(wavs)} items in {gen_time:.2f}s")

        audios_b64 = [numpy_to_base64(w, sr) for w in wavs]
        return {
            "audios": audios_b64,
            "sample_rate": sr,
            "generation_time": gen_time,
        }
    except Exception as e:
        logger.error(f"CustomVoice batch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── VoiceDesign Endpoints ──────────────────────────────────────

@app.post("/generate/voice-design")
async def generate_voice_design(req: GenerateVoiceDesignRequest):
    """生成 VoiceDesign 语音，返回 WAV bytes"""
    t0 = time.time()
    try:
        model = get_model("voice_design")
        wavs, sr = model.generate_voice_design(
            text=req.text,
            language=req.language,
            instruct=req.instruct,
        )

        gen_time = time.time() - t0
        logger.info(f"VoiceDesign generated in {gen_time:.2f}s, sr={sr}")

        audio_data = wavs[0] if isinstance(wavs, list) else wavs
        wav_bytes = numpy_to_wav_bytes(audio_data, sr)
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "X-Sample-Rate": str(sr),
                "X-Generation-Time": f"{gen_time:.3f}",
                "X-Audio-Duration": f"{len(audio_data) / sr:.3f}",
            },
        )
    except Exception as e:
        logger.error(f"VoiceDesign generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate/voice-design/batch")
async def generate_voice_design_batch(req: GenerateVoiceDesignRequest):
    """批量生成 VoiceDesign"""
    t0 = time.time()
    try:
        model = get_model("voice_design")
        wavs, sr = model.generate_voice_design(
            text=req.text,
            language=req.language,
            instruct=req.instruct,
        )

        gen_time = time.time() - t0
        logger.info(f"VoiceDesign batch generated {len(wavs)} items in {gen_time:.2f}s")

        audios_b64 = [numpy_to_base64(w, sr) for w in wavs]
        return {
            "audios": audios_b64,
            "sample_rate": sr,
            "generation_time": gen_time,
        }
    except Exception as e:
        logger.error(f"VoiceDesign batch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Voice Clone (Base Model) Endpoints ─────────────────────────

@app.post("/create-voice-clone-prompt")
async def create_voice_clone_prompt(req: CreateVoiceClonePromptRequest):
    """从参考音频创建 voice clone prompt，返回序列化后的 prompt"""
    t0 = time.time()
    try:
        model = get_model("base")

        # 解码音频
        audio_bytes = base64.b64decode(req.ref_audio_base64)
        audio_data, sample_rate = wav_bytes_to_numpy(audio_bytes)

        prompt = model.create_voice_clone_prompt(
            ref_audio=(audio_data, sample_rate),
            ref_text=req.ref_text if not req.x_vector_only_mode else None,
            x_vector_only_mode=req.x_vector_only_mode,
        )

        prompt_b64 = serialize_prompt(prompt)
        gen_time = time.time() - t0
        logger.info(f"Voice clone prompt created in {gen_time:.2f}s")

        return {
            "voice_clone_prompt_b64": prompt_b64,
            "generation_time": gen_time,
        }
    except Exception as e:
        logger.error(f"Create voice clone prompt failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate/voice-clone")
async def generate_voice_clone(req: GenerateVoiceCloneRequest):
    """使用 voice clone prompt 生成语音，返回 WAV bytes"""
    t0 = time.time()
    try:
        model = get_model("base")

        prompt = deserialize_prompt(req.voice_clone_prompt_b64)

        wavs, sr = model.generate_voice_clone(
            text=req.text,
            language=req.language,
            voice_clone_prompt=prompt,
        )

        gen_time = time.time() - t0
        logger.info(f"Voice clone generated in {gen_time:.2f}s, sr={sr}")

        audio_data = wavs[0] if isinstance(wavs, list) else wavs
        wav_bytes = numpy_to_wav_bytes(audio_data, sr)
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "X-Sample-Rate": str(sr),
                "X-Generation-Time": f"{gen_time:.3f}",
                "X-Audio-Duration": f"{len(audio_data) / sr:.3f}",
            },
        )
    except Exception as e:
        logger.error(f"Voice clone generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Streaming Endpoints (SSE) ──────────────────────────────────

async def stream_audio_sse(audio_data: np.ndarray, sr: int, gen_time: float):
    """将音频分块以 SSE 形式流式传输"""
    import json as _json

    # metadata
    metadata = {
        "sample_rate": sr,
        "generation_time": gen_time,
        "audio_duration": len(audio_data) / sr,
    }
    yield {"event": "metadata", "data": _json.dumps(metadata)}

    # audio chunks
    chunk_duration = 0.5
    chunk_samples = int(sr * chunk_duration)
    for start in range(0, len(audio_data), chunk_samples):
        end = min(start + chunk_samples, len(audio_data))
        chunk = audio_data[start:end]
        chunk_b64 = numpy_to_base64(chunk, sr)
        yield {"event": "audio", "data": chunk_b64}
        await asyncio.sleep(0.01)

    yield {"event": "done", "data": "complete"}


@app.post("/generate/custom-voice/stream")
async def generate_custom_voice_stream(req: GenerateCustomVoiceRequest):
    """流式生成 CustomVoice"""
    t0 = time.time()
    try:
        model = get_model("custom_voice")
        wavs, sr = model.generate_custom_voice(
            text=req.text, language=req.language, speaker=req.speaker, instruct=req.instruct,
        )
        gen_time = time.time() - t0
        audio_data = wavs[0] if isinstance(wavs, list) else wavs
        return EventSourceResponse(stream_audio_sse(audio_data, sr, gen_time))
    except Exception as e:
        logger.error(f"CustomVoice stream failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate/voice-design/stream")
async def generate_voice_design_stream(req: GenerateVoiceDesignRequest):
    """流式生成 VoiceDesign"""
    t0 = time.time()
    try:
        model = get_model("voice_design")
        wavs, sr = model.generate_voice_design(
            text=req.text, language=req.language, instruct=req.instruct,
        )
        gen_time = time.time() - t0
        audio_data = wavs[0] if isinstance(wavs, list) else wavs
        return EventSourceResponse(stream_audio_sse(audio_data, sr, gen_time))
    except Exception as e:
        logger.error(f"VoiceDesign stream failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate/voice-clone/stream")
async def generate_voice_clone_stream(req: GenerateVoiceCloneRequest):
    """流式生成 Voice Clone"""
    t0 = time.time()
    try:
        model = get_model("base")
        prompt = deserialize_prompt(req.voice_clone_prompt_b64)
        wavs, sr = model.generate_voice_clone(
            text=req.text, language=req.language, voice_clone_prompt=prompt,
        )
        gen_time = time.time() - t0
        audio_data = wavs[0] if isinstance(wavs, list) else wavs
        return EventSourceResponse(stream_audio_sse(audio_data, sr, gen_time))
    except Exception as e:
        logger.error(f"Voice clone stream failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Main ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "worker:app",
        host=WORKER_HOST,
        port=WORKER_PORT,
        reload=False,
    )
