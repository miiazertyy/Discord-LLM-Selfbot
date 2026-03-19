import io
import math
import struct
import base64
import asyncio
import aiohttp


def _wav_to_ogg_opus(wav_bytes: bytes) -> tuple:
    """Convert WAV bytes to OGG Opus, returns (ogg_bytes, duration_secs)."""
    import subprocess, json

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-f", "wav", "-i", "pipe:0"],
        input=wav_bytes, capture_output=True,
    )
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        duration = 1.0

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "wav", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1"],
        input=wav_bytes, capture_output=True,
    )
    if result.returncode != 0:
        raise Exception(f"ffmpeg conversion failed: {result.stderr.decode()}")
    return result.stdout, duration


def _get_wav_duration(audio_bytes: bytes) -> float:
    """Extract duration in seconds from a WAV file header."""
    try:
        if audio_bytes[:4] != b'RIFF':
            return 1.0
        sample_rate = struct.unpack_from('<I', audio_bytes, 24)[0]
        num_channels = struct.unpack_from('<H', audio_bytes, 22)[0]
        bits_per_sample = struct.unpack_from('<H', audio_bytes, 34)[0]
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        offset = 12
        while offset < len(audio_bytes) - 8:
            chunk_id = audio_bytes[offset:offset+4]
            chunk_size = struct.unpack_from('<I', audio_bytes, offset + 4)[0]
            if chunk_id == b'data':
                return chunk_size / byte_rate
            offset += 8 + chunk_size
        return 1.0
    except Exception:
        return 1.0


def _make_waveform(audio_bytes: bytes, duration: float) -> str:
    """Generate waveform string from WAV PCM data (same math as Vencord)."""
    try:
        offset = 12
        data_start = None
        data_size = 0
        while offset < len(audio_bytes) - 8:
            chunk_id = audio_bytes[offset:offset+4]
            chunk_size = struct.unpack_from('<I', audio_bytes, offset + 4)[0]
            if chunk_id == b'data':
                data_start = offset + 8
                data_size = chunk_size
                break
            offset += 8 + chunk_size

        if data_start is None:
            return base64.b64encode(b'\x00' * 32).decode()

        bits_per_sample = struct.unpack_from('<H', audio_bytes, 34)[0]
        num_channels = struct.unpack_from('<H', audio_bytes, 22)[0]
        pcm = audio_bytes[data_start:data_start + data_size]

        if bits_per_sample == 16:
            samples = [
                struct.unpack_from('<h', pcm, i)[0] / 32768.0
                for i in range(0, len(pcm) - 1, 2 * num_channels)
            ]
        else:
            samples = [(b / 128.0 - 1.0) for b in pcm[::num_channels]]

        num_bins = max(32, min(256, int(duration * 10)))
        samples_per_bin = max(1, len(samples) // num_bins)

        bins = []
        for b in range(num_bins):
            start = b * samples_per_bin
            chunk = samples[start:start + samples_per_bin]
            if not chunk:
                bins.append(0)
                continue
            rms = math.sqrt(sum(s ** 2 for s in chunk) / len(chunk))
            bins.append(int(rms * 255))

        max_bin = max(bins) if bins else 1
        if max_bin == 0:
            max_bin = 1
        ratio = 1 + (255 / max_bin - 1) * min(1.0, 100 * (max_bin / 255) ** 3)
        bins = [min(255, int(b * ratio)) for b in bins]

        return base64.b64encode(bytes(bins)).decode()
    except Exception:
        return base64.b64encode(b'\x00' * 32).decode()


def _snowflake_now() -> int:
    import time
    DISCORD_EPOCH = 1420070400000
    ms = int(time.time() * 1000)
    return ((ms - DISCORD_EPOCH) << 22)


async def send_voice_message(channel, wav_bytes: bytes, reply_to=None, mention_author=True):
    """
    Send audio as a proper Discord voice message bubble.
    Converts WAV -> OGG Opus via ffmpeg, then uses the Discord attachment
    upload API with flags=1<<13, waveform and duration_secs (same as Vencord).
    """
    # Compute waveform from WAV PCM, get accurate duration from ffprobe
    ogg_bytes, duration = await asyncio.get_event_loop().run_in_executor(
        None, _wav_to_ogg_opus, wav_bytes
    )
    waveform = _make_waveform(wav_bytes, duration)

    token = channel._state.http.token
    channel_id = channel.id

    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        # Step 1: request upload slot
        upload_resp = await session.post(
            f"https://discord.com/api/v9/channels/{channel_id}/attachments",
            headers=headers,
            json={
                "files": [{
                    "filename": "voice-message.ogg",
                    "file_size": len(ogg_bytes),
                    "id": "0",
                }]
            }
        )
        upload_data = await upload_resp.json()

        if "attachments" not in upload_data:
            raise Exception(f"Failed to get upload URL: {upload_data}")

        attachment = upload_data["attachments"][0]
        upload_url = attachment["upload_url"]
        uploaded_filename = attachment["upload_filename"]

        # Step 2: upload OGG bytes to CDN
        put_resp = await session.put(
            upload_url,
            data=ogg_bytes,
            headers={"Content-Type": "audio/ogg"},
        )

        # Step 3: send the message with voice message flag
        body = {
            "flags": 1 << 13,
            "channel_id": str(channel_id),
            "content": "",
            "nonce": str(_snowflake_now()),
            "sticker_ids": [],
            "type": 0,
            "attachments": [{
                "id": "0",
                "filename": "voice-message.ogg",
                "uploaded_filename": uploaded_filename,
                "waveform": waveform,
                "duration_secs": duration,
            }],
        }

        if reply_to:
            body["message_reference"] = {
                "channel_id": str(channel_id),
                "message_id": str(reply_to.id),
            }
            body["allowed_mentions"] = {
                "replied_user": mention_author,
                "parse": ["users", "roles"],
            }

        msg_resp = await session.post(
            f"https://discord.com/api/v9/channels/{channel_id}/messages",
            headers=headers,
            json=body,
        )
        result = await msg_resp.json()

        if "id" not in result:
            raise Exception(f"Failed to send voice message: {result}")

        return result
