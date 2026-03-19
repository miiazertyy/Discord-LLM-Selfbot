import io
import math
import struct
import base64
import aiohttp

async def send_voice_message(channel, audio_bytes: bytes, reply_to=None, mention_author=True):
    """
    Send audio as a proper Discord voice message bubble.
    Mimics what Vencord does: uploads the file then posts with flags=1<<13,
    waveform and duration_secs so Discord renders it as a voice message.
    """
    duration = _get_wav_duration(audio_bytes)
    waveform = _make_waveform(audio_bytes, duration)

    # Step 1: request an upload URL from Discord
    token = channel._state.http.token
    channel_id = channel.id

    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        # Request attachment upload slot
        upload_resp = await session.post(
            f"https://discord.com/api/v9/channels/{channel_id}/attachments",
            headers=headers,
            json={
                "files": [{
                    "filename": "voice-message.ogg",
                    "file_size": len(audio_bytes),
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

        # Step 2: upload the raw audio bytes to the CDN URL
        await session.put(
            upload_url,
            data=audio_bytes,
            headers={"Content-Type": "audio/ogg"},
        )

        # Step 3: post the message with voice message flags
        body = {
            "flags": 1 << 13,  # IS_VOICE_MESSAGE
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


def _get_wav_duration(audio_bytes: bytes) -> float:
    """Extract duration in seconds from a WAV file header."""
    try:
        # WAV header: ChunkSize at 4, fmt subchunk starts at 12
        # SampleRate at 24, NumChannels at 22, BitsPerSample at 34
        # DataSize: find 'data' chunk
        if audio_bytes[:4] != b'RIFF':
            return 1.0

        sample_rate = struct.unpack_from('<I', audio_bytes, 24)[0]
        num_channels = struct.unpack_from('<H', audio_bytes, 22)[0]
        bits_per_sample = struct.unpack_from('<H', audio_bytes, 34)[0]
        byte_rate = sample_rate * num_channels * bits_per_sample // 8

        # Find the data chunk
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
    """
    Generate a waveform string from WAV PCM data.
    Same approach as Vencord: bin the samples, RMS per bin, normalize, base64.
    """
    try:
        # Find the data chunk
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
    """Generate a Discord snowflake for the current timestamp."""
    import time
    DISCORD_EPOCH = 1420070400000
    ms = int(time.time() * 1000)
    return ((ms - DISCORD_EPOCH) << 22)
