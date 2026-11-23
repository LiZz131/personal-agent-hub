"""自检 .venv：关键依赖版本，以及（装了视频入库模块时）ctranslate2 能否用上 GPU。"""
import importlib.metadata as m
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
sys.stdout.reconfigure(encoding="utf-8")

for p in ["lark-oapi", "pyyaml", "yt-dlp", "faster-whisper", "ctranslate2", "av", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]:
    try:
        print(f"  {p:20s} {m.version(p)}")
    except m.PackageNotFoundError:
        print(f"  {p:20s} 未安装")

try:
    from video import load_cuda_dlls  # noqa: E402  可选模块（视频入库）
except ImportError:
    print("  视频入库模块（scripts/video.py）未安装，跳过 GPU 自检")
    sys.exit(0)

load_cuda_dlls()
import ctranslate2  # noqa: E402

n = ctranslate2.get_cuda_device_count()
types = sorted(ctranslate2.get_supported_compute_types("cuda")) if n else []
print(f"  CUDA 设备数: {n}；支持的计算类型: {types or '-'}")
