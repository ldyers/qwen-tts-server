"""
抖音直播弹幕路由
- 弹幕抓取在独立子进程中运行，通过 stdout JSON 行通信
- TTS 播报由前端浏览器处理
- 子进程用 start_new_session 完全隔离
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/danmu", tags=["danmu"])

DANMU_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "danmu", "fetcher.py")
DANMU_SCRIPT = os.path.abspath(DANMU_SCRIPT)


class DanmuManager:
    """弹幕管理器 - 子进程方式，非阻塞读取"""

    def __init__(self):
        self.process = None
        self.is_running = False
        self.live_id = ""
        self.danmu_list: List[Dict] = []
        self.auto_tts = False
        self.tts_speaker = "Serena"
        self.tts_language = "Chinese"
        self.tts_speed = 1.0
        self.stats = {"total": 0, "unique_users": set(), "start_time": None}
        self._lock = threading.Lock()
        self._reader_thread = None

    def start(self, live_id: str):
        with self._lock:
            if self.is_running:
                self.stop()

            live_id = str(live_id).strip()
            if 'live.douyin.com/' in live_id:
                live_id = live_id.split('live.douyin.com/')[-1].split('?')[0].split('/')[0]

            self.live_id = live_id
            self.is_running = True
            self.danmu_list.clear()
            self.stats = {"total": 0, "unique_users": set(), "start_time": datetime.now().isoformat()}

            env = os.environ.copy()
            env["DANMU_LIVE_ID"] = live_id
            env["DANMU_TTS_ENABLED"] = str(self.auto_tts)

            self.process = subprocess.Popen(
                [sys.executable, "-u", DANMU_SCRIPT],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,  # 用 bytes 模式，避免编码问题
                start_new_session=True,
            )

            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

    def _read_output(self):
        """非阻塞读取子进程 stdout"""
        if not self.process:
            return
        try:
            buf = b""
            while True:
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                        mtype = msg.get("type", "")
                        if mtype == "danmu":
                            d = msg.get("data", {})
                            self.danmu_list.append(d)
                            if len(self.danmu_list) > 500:
                                self.danmu_list = self.danmu_list[-500:]
                            self.stats["total"] += 1
                            if d.get("user_name"):
                                self.stats["unique_users"].add(d["user_name"])
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            self.is_running = False

    def stop(self):
        with self._lock:
            if self.process:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    self.process.wait(timeout=3)
                except Exception:
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    except Exception:
                        pass
                self.process = None
            self.is_running = False

    def get_status(self) -> dict:
        if self.process and self.process.poll() is not None:
            self.is_running = False
        return {
            "is_running": self.is_running,
            "live_id": self.live_id,
            "total": self.stats["total"],
            "unique_users": len(self.stats["unique_users"]),
            "start_time": self.stats.get("start_time"),
            "auto_tts": self.auto_tts,
            "tts_speaker": self.tts_speaker,
            "tts_language": self.tts_language,
            "tts_speed": self.tts_speed,
            "history_count": len(self.danmu_list),
        }

    def get_new_danmu(self, after_index: int = 0) -> list:
        """获取指定索引之后的新弹幕"""
        if after_index >= len(self.danmu_list):
            return []
        return self.danmu_list[after_index:]

    def set_tts_config(self, enabled=None, speaker=None, language=None, speed=None):
        changed = False
        if enabled is not None and enabled != self.auto_tts:
            self.auto_tts = enabled
            changed = True
        if speaker is not None and speaker != self.tts_speaker:
            self.tts_speaker = speaker
            changed = True
        if language is not None and language != self.tts_language:
            self.tts_language = language
            changed = True
        if speed is not None and speed != self.tts_speed:
            self.tts_speed = speed
            changed = True
        if changed and self.is_running and self.live_id:
            self.start(self.live_id)


danmu_manager = DanmuManager()


class StartDanmuRequest(BaseModel):
    live_id: str

class TTSConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    speaker: Optional[str] = None
    language: Optional[str] = None
    speed: Optional[float] = None

@router.post("/start")
async def start_danmu(req: StartDanmuRequest):
    if not req.live_id:
        raise HTTPException(status_code=400, detail="live_id 不能为空")
    danmu_manager.start(req.live_id)
    return {"status": "started", "live_id": req.live_id}

@router.post("/stop")
async def stop_danmu():
    danmu_manager.stop()
    return {"status": "stopped"}

@router.get("/status")
async def danmu_status():
    return danmu_manager.get_status()

@router.get("/history")
async def danmu_history(limit: int = 100):
    return {"danmu": danmu_manager.danmu_list[-limit:]}

@router.post("/tts-config")
async def set_tts_config(req: TTSConfigRequest):
    danmu_manager.set_tts_config(
        enabled=req.enabled, speaker=req.speaker,
        language=req.language, speed=req.speed,
    )
    return danmu_manager.get_status()

@router.websocket("/ws")
async def danmu_websocket(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "status", "data": danmu_manager.get_status()})
    
    # 发送历史
    for msg in danmu_manager.danmu_list[-50:]:
        await websocket.send_json({"type": "danmu", "data": msg})
    
    last_index = len(danmu_manager.danmu_list)
    try:
        while True:
            await asyncio.sleep(2)
            # 检查新弹幕
            current_len = len(danmu_manager.danmu_list)
            if current_len > last_index:
                for msg in danmu_manager.danmu_list[last_index:]:
                    if danmu_manager.auto_tts and msg.get("content"):
                        msg["tts_enabled"] = True
                    await websocket.send_json({"type": "danmu", "data": msg})
                last_index = current_len
            # 心跳
            await websocket.send_json({"type": "heartbeat", "data": {"total": danmu_manager.stats["total"]}})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
