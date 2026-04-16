"""
AES-128-ECB encryption/decryption for iLink CDN media files.
"""

import os
import logging
import base64
from Crypto.Cipher import AES

log = logging.getLogger("wechat.media")


def _pad_pkcs7(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _unpad_pkcs7(data: bytes) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len == 0 or pad_len > 16:
        log.debug("PKCS7 unpad: invalid pad byte %d, returning raw data", pad_len)
        return data
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        log.debug("PKCS7 unpad: padding bytes mismatch, returning raw data")
        return data
    return data[:-pad_len]


def generate_aes_key() -> bytes:
    return os.urandom(16)


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(_pad_pkcs7(plaintext))


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return _unpad_pkcs7(cipher.decrypt(ciphertext))


def key_to_b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


def b64_to_key(b64_str: str) -> bytes:
    return base64.b64decode(b64_str)


def audio_to_silk(audio_bytes: bytes, input_ext: str = ".mp3") -> tuple[bytes, int]:
    """
    Convert audio (MP3/WAV/etc.) to WeChat SILK format via ffmpeg + pysilk.
    Returns (silk_bytes, duration_ms). Returns (b"", 0) on failure.
    """
    import io
    import subprocess
    import tempfile

    try:
        import pysilk
    except ImportError:
        log.warning("pysilk not installed, cannot convert to SILK")
        return b"", 0

    with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        tmp_in_path = tmp_in.name

    tmp_pcm_path = tmp_in_path + ".pcm"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in_path,
             "-f", "s16le", "-ar", "24000", "-ac", "1",
             tmp_pcm_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            log.error("ffmpeg conversion failed: %s", result.stderr.decode()[:300])
            return b"", 0

        # Re-sample to 16kHz — WeChat native voice recording rate.
        # 24kHz SILK causes silent playback on WeChat clients.
        SILK_RATE = 16000
        tmp_pcm16_path = tmp_in_path + ".pcm16"
        result16 = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in_path,
             "-f", "s16le", "-ar", str(SILK_RATE), "-ac", "1",
             tmp_pcm16_path],
            capture_output=True, timeout=30,
        )
        if result16.returncode == 0:
            with open(tmp_pcm16_path, "rb") as f:
                pcm_data = f.read()
            try:
                os.unlink(tmp_pcm16_path)
            except OSError:
                pass
        else:
            log.warning("ffmpeg 16kHz resample failed, using 24kHz PCM")

        duration_ms = int(len(pcm_data) / (SILK_RATE * 2) * 1000)

        pcm_input = io.BytesIO(pcm_data)
        silk_output = io.BytesIO()
        # tencent=True outputs WeChat-compatible SILK (0x02 + #!SILK_V3 ...)
        pysilk.encode(pcm_input, silk_output, SILK_RATE, SILK_RATE, tencent=True)
        silk_bytes = silk_output.getvalue()

        log.info("Audio→SILK: %d→%d bytes, %dms @%dHz, header=%s",
                 len(audio_bytes), len(silk_bytes), duration_ms, SILK_RATE,
                 silk_bytes[:12].hex())
        return silk_bytes, duration_ms
    except FileNotFoundError:
        log.error("ffmpeg not found, cannot convert audio to SILK")
        return b"", 0
    except Exception:
        log.exception("Audio→SILK conversion failed")
        return b"", 0
    finally:
        for p in (tmp_in_path, tmp_pcm_path):
            try:
                os.unlink(p)
            except OSError:
                pass
