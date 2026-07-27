# Qwen3-TTS Server 分离式部署指南
## VPS (业务层) + Windows GPU (推理层) via WireGuard

```
[客户端] ──HTTPS──> [VPS 124.221.52.32:8000] ──WG隧道──> [Windows 10.100.0.2:8001]
                     业务层(API/鉴权/Demo/预处理)          GPU Worker(加载模型/推理)
                     不需要torch/qwen-tts                   RTX 3060 12GB
```

## 架构说明

| 组件 | 位置 | 端口 | 说明 |
|------|------|------|------|
| **VPS 业务层** | 腾讯云 CVM (Ubuntu) | 8000 | FastAPI, API 鉴权, Demo页面, 音频预处理 |
| **GPU Worker** | Windows PC (RTX 3060) | 8001 | 加载 Qwen3-TTS 模型, 执行推理 |
| **WireGuard** | 10.100.0.0/24 | - | VPS(10.100.0.1) <-> Windows(10.100.0.2) |

### 数据流

1. 客户端 → VPS:8000 (HTTPS/API请求)
2. VPS 预处理音频(裁剪/去噪/归一化)
3. VPS → Worker:8001 (HTTP via WireGuard, ~11ms 延迟)
4. Worker 加载模型(首次), 执行推理, 返回 WAV
5. VPS 格式化响应, 返回客户端

---

## 第一部分: VPS 部署 (已完成)

VPS 侧已部署在 `/home/ubuntu/qwen-tts-server/`。

### 配置文件

`.env` 关键配置:
```bash
REMOTE_MODE=true                          # 启用远程模式
WORKER_URL=http://10.100.0.2:8001         # Windows GPU Worker 地址
WORKER_API_KEY=                           # Worker 鉴权密钥(可选)
WORKER_TIMEOUT=120                        # 超时(秒)
API_KEYS=                                 # 对外 API Key(可选)
```

### 启动 VPS 服务

```bash
cd /home/ubuntu/qwen-tts-server
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 验证

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/custom-voice/speakers
```

---

## 第二部分: Windows GPU Worker 部署

### 前提条件

1. **Python 3.10+** - https://python.org (勾选 "Add to PATH")
2. **NVIDIA 驱动** - 已安装 (能运行 `nvidia-smi`)
3. **WireGuard 隧道** - 已连接, 能 ping 通 VPS
4. **Git** (可选) - 用于克隆代码

### 步骤 1: 获取代码

**方式 A: 从 VPS 下载 (推荐)**

在 Windows PowerShell 中:
```powershell
# 通过 WireGuard 从 VPS 下载 worker 目录
scp -r ubuntu@10.100.0.1:/home/ubuntu/qwen-tts-server/worker C:\qwen-tts-worker\
```

或者用浏览器访问 VPS 上的打包文件(见下方"打包下载")。

**方式 B: 直接创建文件**

将 `worker/` 目录下的以下文件复制到 Windows:
- `worker.py` - Worker 服务主程序
- `start_worker.py` - 启动脚本
- `requirements.txt` - 依赖列表
- `install_worker.bat` - 一键安装
- `start_worker.bat` - 启动(懒加载)
- `start_worker_preload.bat` - 启动(预加载)

### 步骤 2: 安装依赖

双击运行 `install_worker.bat`, 或在 PowerShell 中:
```powershell
cd C:\qwen-tts-worker
.\install_worker.ps1
```

脚本会自动:
1. 创建 Python 虚拟环境
2. 安装 PyTorch (CUDA 12.1)
3. 安装 Worker 依赖
4. (可选) 安装 Flash Attention

### 步骤 3: 配置 WireGuard IP

确认 Windows 的 WireGuard IP 是 `10.100.0.2`:
```powershell
ipconfig | findstr "10.100"
```

如果不是, 修改 VPS 的 `.env` 文件中的 `WORKER_URL`。

### 步骤 4: 启动 Worker

**懒加载模式 (推荐首次使用):**
```powershell
.\start_worker.bat
```
首次请求时会下载模型(~3.5GB), 之后缓存在本地。

**预加载模式:**
```powershell
.\start_worker_preload.bat
```
启动时加载所有模型, 首次请求更快。

### 步骤 5: 验证 Worker

```powershell
curl http://localhost:8001/health
```

预期输出:
```json
{
  "status": "healthy",
  "models_loaded": {
    "custom_voice": false,
    "voice_design": false,
    "base": false
  },
  "gpu": {
    "gpu_name": "NVIDIA GeForce RTX 3060",
    "gpu_memory_allocated_mb": 0,
    "gpu_memory_total_mb": 12288
  }
}
```

### 步骤 6: 端到端测试

在 VPS 上测试:
```bash
curl -X POST http://localhost:8000/api/v1/custom-voice/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好，这是语音合成测试。",
    "language": "Chinese",
    "speaker": "Serena",
    "response_format": "wav"
  }' \
  --output test.wav

# 播放
aplay test.wav
```

---

## 常见问题

### Q: 模型下载到哪里？

模型缓存在 Windows 的 HuggingFace 默认缓存目录:
- `%USERPROFILE%\.cache\huggingface\hub\`

可以通过环境变量修改:
```powershell
set HF_HOME=D:\models
.\start_worker.bat
```

### Q: 首次请求很慢？

首次请求会下载模型(~3.5GB/模型), 之后缓存在本地。
建议使用 `--preload` 预加载常用模型。

### Q: Flash Attention 安装失败？

Flash Attention 在 Windows 上编译较困难。如果失败:
1. 设置 `USE_FLASH_ATTENTION=false`
2. Worker 会自动回退到标准 attention

### Q: WireGuard 连接断了怎么办？

VPS 服务会继续运行, 但 TTS 请求会返回 500 错误。
Worker 重连后自动恢复。

### Q: 如何设置 API 鉴权？

**VPS 对外鉴权:**
编辑 `.env`:
```bash
API_KEYS=my-secret-key-1,my-secret-key-2
```

**VPS <-> Worker 鉴权:**
VPS `.env`:
```bash
WORKER_API_KEY=shared-worker-secret
```
Windows 启动:
```powershell
set WORKER_API_KEY=shared-worker-secret
.\start_worker.bat
```

---

## 端口和防火墙

| 端口 | 位置 | 用途 | 是否对外 |
|------|------|------|---------|
| 8000 | VPS | API 服务 | 是 (公网) |
| 8001 | Windows | GPU Worker | 否 (仅 WG 内网) |
| 51820 | VPS | WireGuard | 是 (UDP) |

Windows 不需要在公网暴露任何端口, Worker 仅监听 WireGuard 内网。
