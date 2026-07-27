"""
Windows GPU Worker 启动脚本（不需要 conda）
用法:
    python start_worker.py
    python start_worker.py --preload        # 启动时预加载所有模型
    python start_worker.py --port 8001      # 自定义端口
"""
import sys
import os
import argparse

def main():
    parser = argparse.ArgumentParser(description="Qwen3-TTS GPU Worker")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8001, help="Port")
    parser.add_argument("--preload", action="store_true", help="Preload all models on startup")
    parser.add_argument("--no-warmup", action="store_true", help="Skip warmup")
    parser.add_argument("--device", default="cuda:0", help="CUDA device (cuda:0, cpu)")
    args = parser.parse_args()

    # 设置环境变量
    os.environ["WORKER_HOST"] = args.host
    os.environ["WORKER_PORT"] = str(args.port)
    os.environ["CUDA_DEVICE"] = args.device
    if args.preload:
        os.environ["PRELOAD_MODELS"] = "true"
    if args.no_warmup:
        os.environ["ENABLE_WARMUP"] = "false"

    print("=" * 60)
    print("  Qwen3-TTS GPU Worker")
    print(f"  Host: {args.host}:{args.port}")
    print(f"  Device: {args.device}")
    print(f"  Preload: {args.preload}")
    print("=" * 60)

    import uvicorn
    uvicorn.run(
        "worker:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
