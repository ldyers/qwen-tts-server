#!/usr/bin/env python
"""
弹幕抓取子进程
从环境变量读取配置，连接抖音直播间 WebSocket，输出 JSON 行到 stdout
主进程通过读取 stdout 获取弹幕数据
"""
import json
import os
import sys
import threading
import time
from datetime import datetime

# 确保能找到 danmu 模块
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
# 也加上项目根目录
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)

from liveMan import DouyinLiveWebFetcher
from protobuf.douyin import ChatMessage, PushFrame


def output(msg_type, data=None, message=None):
    """输出 JSON 行到 stdout"""
    msg = {"type": msg_type, "data": data or {}, "message": message or "", "ts": datetime.now().isoformat()}
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def main():
    live_id = os.environ.get("DANMU_LIVE_ID", "").strip()
    if not live_id:
        output("error", message="DANMU_LIVE_ID not set")
        sys.exit(1)

    tts_enabled = os.environ.get("DANMU_TTS_ENABLED", "false").lower() == "true"
    tts_speaker = os.environ.get("DANMU_TTS_SPEAKER", "Serena")
    tts_language = os.environ.get("DANMU_TTS_LANGUAGE", "Chinese")
    tts_speed = float(os.environ.get("DANMU_TTS_SPEED", "1.0"))

    output("status", message=f"Starting danmu fetcher for {live_id}, TTS={tts_enabled}")

    # TTS 由前端浏览器处理，子进程不生成音频
    # 子进程只负责标记哪些弹幕需要 TTS

    fetcher = DouyinLiveWebFetcher(live_id)

    # 替换聊天消息处理
    def custom_parse_chat(payload):
        try:
            message = ChatMessage().parse(payload)
            user_name = message.user.nick_name
            user_id = message.user.id
            content = message.content

            danmu = {
                "type": "chat",
                "user_name": user_name,
                "user_id": str(user_id),
                "content": content,
                "ts": datetime.now().strftime("%H:%M:%S"),
            }
            output("danmu", danmu)

            # TTS 标记（前端浏览器负责生成音频）
            if tts_enabled and content:
                danmu["tts_enabled"] = True
        except Exception:
            pass

    fetcher._parseChatMsg = custom_parse_chat

    # 禁用其他消息
    fetcher._parseGiftMsg = lambda p: None
    fetcher._parseLikeMsg = lambda p: None
    fetcher._parseMemberMsg = lambda p: None
    fetcher._parseSocialMsg = lambda p: None
    fetcher._parseRoomUserSeqMsg = lambda p: None
    fetcher._parseFansclubMsg = lambda p: None
    fetcher._parseEmojiChatMsg = lambda p: None
    fetcher._parseRoomMsg = lambda p: None
    fetcher._parseRoomStatsMsg = lambda p: None
    fetcher._parseRankMsg = lambda p: None
    fetcher._parseRoomStreamAdaptationMsg = lambda p: None

    # 静默心跳
    def quiet_heartbeat(self):
        while True:
            try:
                heartbeat = PushFrame(payload_type='hb').SerializeToString()
                self.ws.send(heartbeat, 0x9)
            except Exception:
                break
            time.sleep(5)

    fetcher._sendHeartbeat = lambda: quiet_heartbeat(fetcher)

    # 静默 WebSocket 事件
    fetcher._wsOnError = lambda ws, error: output("error", message=str(error))
    fetcher._wsOnClose = lambda ws, *args: output("status", message="WebSocket closed")

    try:
        fetcher.start()
    except Exception as e:
        output("error", message=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
