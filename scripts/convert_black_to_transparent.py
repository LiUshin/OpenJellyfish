"""
将纯黑背景的 MP4 视频转换为背景透明的 WebM (VP9 + alpha) 视频。
使用 imageio-ffmpeg 自带的 FFmpeg 二进制文件，无需系统安装 FFmpeg。
"""

import subprocess
import sys
from pathlib import Path

try:
    from imageio_ffmpeg import get_ffmpeg_exe
except ImportError:
    print("请先安装 imageio-ffmpeg: pip install imageio-ffmpeg")
    sys.exit(1)


def convert_black_to_transparent(
    input_path: str,
    output_path: str,
    similarity: float = 0.12,
    blend: float = 0.18,
):
    """
    用 FFmpeg colorkey 滤镜把黑色背景替换为透明，输出 VP9 WebM。

    similarity: 颜色匹配容差 (0~1)，值越大去除的黑色范围越广
    blend:      边缘过渡 (0~1)，值越大边缘越柔和
    """
    ffmpeg = get_ffmpeg_exe()
    inp = Path(input_path)
    out = Path(output_path)

    if not inp.exists():
        print(f"[ERROR] Input file not found: {inp}")
        sys.exit(1)

    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(inp),
        "-vf", f"colorkey=black:{similarity}:{blend}",
        "-pix_fmt", "yuva420p",
        "-c:v", "libvpx-vp9",
        "-crf", "30",
        "-b:v", "0",
        "-auto-alt-ref", "0",
        "-an",
        str(out),
    ]

    print(f"[Converting] {inp.name} -> {out.name}")
    print(f"  similarity={similarity}, blend={blend}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[ERROR] FFmpeg failed:\n{result.stderr}")
        sys.exit(1)

    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"[DONE] Output: {out}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent / "frontend" / "public" / "media_resources"
    input_file = base / "loadingGif.mp4"
    output_file = base / "loadingGif.webm"

    convert_black_to_transparent(str(input_file), str(output_file))
