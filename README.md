# Qwen3-TTS 语音合成服务

基于 Qwen3-TTS 模型的语音合成 API 服务，支持 VPS + GPU Worker 分离式部署，通过 WireGuard 隧道实现远程推理。

## 架构

```
客户端 ──HTTP──> VPS:8000 (业务层) ──WireGuard──> Windows:8001 (GPU Worker) ──> RTX 3060
                 FastAPI / WebUI / 鉴权          模型加载 / GPU 推理
                 Docker 容器 (共享网络命名空间)    Python + PyTorch + qwen-tts
```

### 方案 B：共享网络命名空间

TTS 容器通过 `network_mode: container:wg-easy` 直接共享 WireGuard 容器的网络栈，无需主机路由和 NAT，重启自动恢复。

## 功能

- **语音合成**：9 种预设音色（中英日韩），11 种语言，情感控制，语速调节
- **抖音弹幕**：接入 douyin-live-danmu 项目，实时获取直播间弹幕
- **弹幕播报**：聊天弹幕自动 TTS 语音播报，可配置音色/语速/过滤规则
- **WebUI 界面**：音色选择、语速滑块、在线播放、历史记录
- **流式输出**：SSE (Server-Sent Events) 分块传输
- **Docker 部署**：一键 `docker compose up -d`
- **Worker 按需启动**：VPS 通过 SSH 自动唤醒 Windows GPU Worker

## 快速开始

### VPS 侧（Docker 部署）

```bash
# 克隆仓库
git clone https://github.com/ldyers/qwen-tts-server.git
cd qwen-tts-server

# 一键启动（含 WireGuard + TTS 服务）
sudo docker compose -f docker-compose.vps.yml up -d

# 验证
curl http://localhost:8000/health
```

### Windows GPU Worker

详见 [使用文档](http://localhost:8000/docs-page) 的「Windows GPU Worker 启动指南」章节。

快速启动：
```powershell
cd C:\qwen-tts-worker
.\install_worker.bat       # 首次安装
.\run_worker.bat           # 启动 Worker
```

### 使用 API

```bash
# 生成语音
curl -X POST http://124.221.52.32:8000/api/v1/custom-voice/generate \
  -H "Content-Type: application/json" \
  -d '{"text":"你好世界","language":"Chinese","speaker":"Serena"}' \
  --output output.wav
```

WebUI：`http://124.221.52.32:8000/webui`

## 项目结构

```
├── app/                        # VPS 业务层
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置管理（含远程模式）
│   ├── webui.html              # WebUI 页面
│   ├── docs.html               # 使用文档页面
│   ├── models/
│   │   ├── manager.py          # 本地模型管理（原始）
│   │   └── remote_manager.py   # 远程模型管理（VPS 模式）
│   ├── routers/                # API 路由
│   └── utils/                  # 工具函数
├── worker/                     # Windows GPU Worker
│   ├── worker.py               # Worker 服务主程序
│   ├── start_worker.py         # 启动脚本
│   └── requirements.txt        # Worker 依赖
├── scripts/                    # 守护脚本
│   ├── guard_windows.ps1       # 网络+防火墙+SSH 守护
│   └── guard_worker.ps1        # Worker 按需启动守护
├── Dockerfile.vps              # VPS Docker 镜像
├── docker-compose.vps.yml      # 方案B 联合部署
└── requirements-vps.txt        # VPS 依赖（不含 torch）
```

## 配置说明

### VPS 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REMOTE_MODE` | true | 启用远程模式（VPS -> Worker） |
| `WORKER_URL` | http://10.100.0.2:8001 | GPU Worker 地址 |
| `WORKER_TIMEOUT` | 600 | Worker 请求超时（秒） |
| `API_KEYS` | 空 | API 鉴权密钥（逗号分隔，空则不鉴权） |

### Worker 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | 8001 | 监听端口 |
| `--device` | cuda:0 | GPU 设备 |
| `--preload` | 关 | 启动时预加载模型 |
| `HF_ENDPOINT` | https://hf-mirror.com | HuggingFace 镜像 |

## 性能参考

| 配置 | 首次请求 | 后续（5字） | 后续（50字） | 显存 |
|------|---------|------------|-------------|------|
| RTX 3060 12GB bfloat16 | ~28s | ~5s | ~18s | ~5.5GB |
| RTX 3060 12GB + Flash Attn | ~20s | ~3.5s | ~13s | ~4GB |

## 技术栈

- **后端**：FastAPI + Uvicorn + httpx
- **AI 模型**：Qwen3-TTS-12Hz-1.7B (bfloat16)
- **GPU 框架**：PyTorch 2.2.1 + CUDA 12.1
- **网络隧道**：WireGuard (wg-easy)
- **容器化**：Docker + Docker Compose
- **音频处理**：soundfile + pydub + librosa

---

## 版本记录

### v2.1.0 (2026-07-27)

**新增：抖音弹幕获取 + TTS 播报**

- 接入 douyin-live-danmu 项目（独立 Docker 容器，端口 8080）
- 弹幕后台 WebUI：输入直播间号开始/停止抓取，实时弹幕滚动
- 弹幕 TTS 播报：聊天弹幕自动转为语音播报，嵌入弹幕界面
- 播报控制：顶栏一键开始/停止播报，⚙️TTS 按钮打开设置弹窗
- TTS 设置面板：音色选择、语言、语速、过滤规则（跳过纯表情/短弹幕）
- 播报状态面板：实时显示已播报数、已跳过数、队列数、生成耗时、平均耗时
- 串行播放队列（最多 8 条），一条播完再播下一条
- Worker 按需启动：VPS 通过 SSH 自动唤醒 Windows GPU Worker
- TTS 路由使用线程池（run_in_executor），不阻塞 uvicorn 事件循环

### v2.0.0 (2026-07-27)

**架构升级：VPS + GPU Worker 分离式部署**

- 新增 RemoteModelManager：VPS 业务层通过 WireGuard 远程调用 GPU Worker
- 新增 Docker 容器化部署（方案B：共享网络命名空间）
- 新增 WebUI：音色选择、语速调节、在线播放、历史记录
- 新增使用文档页面：Worker 启动、API 流式输出、性能优化、故障排查
- 新增 Windows Worker 守护进程：按需启动、健康检查、自动恢复
- 新增网络守护进程：wg0 接口 Private 模式、防火墙规则、sshd 服务
- 修改 config.py 支持 REMOTE_MODE 配置
- 修改 manager.py 延迟导入 qwen_tts（VPS 端不需要安装 torch）
- 修改 main.py 远程模式下跳过本地 warmup

### v1.1.2 (原始版本)

- 基于原始 qwen-tts-server 项目
- 支持 CustomVoice / VoiceDesign / Base 三种模型
- 支持 FastAPI 服务、API 鉴权、音频预处理
- 单机部署模式（业务逻辑和推理在同一台机器）

---

## 后期升级计划

### v2.2.0 - 大模型对话模块

- **LLM 对话集成**：接入大语言模型（如 Qwen/ChatGLM），支持多轮对话
- **对话转语音**：将 LLM 回复自动转为语音输出，实现语音助手
- **上下文管理**：维护对话历史，支持上下文关联的语音交互
- **角色人设**：为 LLM 设置角色人设，配合不同音色实现角色扮演
- **流式对话**：LLM 边生成边输出，配合 TTS 流式播放降低延迟
- **应用场景**：AI 主播、语音客服、互动陪伴

### v2.3.0 - 流式输出优化

- **真正的流式推理**：基于 Qwen3-TTS 的 token 级流式生成，边推理边输出音频
- **音频分块播放**：客户端接收到第一个音频块即可开始播放，无需等待全部生成
- **WebSocket 推流**：从 HTTP SSE 升级为 WebSocket，支持双向通信和低延迟推流
- **音频队列管理**：多请求并发时的音频队列调度，优先级控制
- **断线重连**：客户端断线后自动重连，从上次位置继续播放
- **应用场景**：实时对话、直播互动、低延迟语音播报
