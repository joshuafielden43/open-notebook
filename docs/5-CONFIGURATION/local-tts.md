# Local Text-to-Speech Setup

Run text-to-speech locally for free, private podcast generation using OpenAI-compatible TTS servers.

---

## Why Local TTS?

| Benefit | Description |
|---------|-------------|
| **Free** | No per-character costs after setup |
| **Private** | Audio never leaves your machine |
| **Unlimited** | No rate limits or quotas |
| **Offline** | Works without internet |

---

## Quick Start with Speaches

[Speaches](https://github.com/speaches-ai/speaches) is an open-source, OpenAI-compatible TTS server.

> **💡 Ready-made Docker Compose files available:**
> - **[docker-compose-speaches.yml](../../examples/docker-compose-speaches.yml)** - Speaches + Open Notebook
> - **[docker-compose-full-local.yml](../../examples/docker-compose-full-local.yml)** - Speaches + Ollama (100% local setup)
>
> These include complete setup instructions and configuration examples. Just copy and run!

### Step 1: Create Docker Compose File

Create a folder and add `docker-compose.yml`:

```yaml
services:
  speaches:
    image: ghcr.io/speaches-ai/speaches:latest-cpu
    container_name: speaches
    ports:
      - "8969:8000"
    volumes:
      - hf-hub-cache:/home/ubuntu/.cache/huggingface/hub
    restart: unless-stopped

volumes:
  hf-hub-cache:
```

### Step 2: Start and Download Model

```bash
# Start Speaches
docker compose up -d

# Wait for startup
sleep 10

# Download voice model (~500MB)
docker compose exec speaches uv tool run speaches-cli model download speaches-ai/Kokoro-82M-v1.0-ONNX
```

### Step 3: Test

```bash
curl "http://localhost:8969/v1/audio/speech" -s \
  -H "Content-Type: application/json" \
  --output test.mp3 \
  --data '{
    "input": "Hello! Local TTS is working.",
    "model": "speaches-ai/Kokoro-82M-v1.0-ONNX",
    "voice": "af_bella"
  }'
```

Play `test.mp3` to verify.

### Step 4: Configure Open Notebook

**Via Settings UI (Recommended):**
1. Go to **Settings** → **API Keys**
2. Click **Add Credential** → Select **OpenAI-Compatible**
3. Enter base URL for TTS: `http://host.docker.internal:8969/v1` (Docker) or `http://localhost:8969/v1` (local)
4. Click **Save**, then **Test Connection**

**Legacy (Deprecated) — Environment variables:**
```yaml
# In your Open Notebook docker-compose.yml
environment:
  - OPENAI_COMPATIBLE_BASE_URL_TTS=http://host.docker.internal:8969/v1
```

```bash
# Local development
export OPENAI_COMPATIBLE_BASE_URL_TTS=http://localhost:8969/v1
```

### Step 5: Add Model in Open Notebook

1. Go to **Settings** → **Models**
2. Click **Add Model** in Text-to-Speech section
3. Configure:
   - **Provider**: `openai_compatible`
   - **Model Name**: `speaches-ai/Kokoro-82M-v1.0-ONNX`
   - **Display Name**: `Local TTS`
4. Click **Save**
5. Set as default if desired

---

## Available Voices

The Kokoro model ships ~50 voices across several accents. Kokoro's own [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) grades each one — most sit at C/D, a handful reach B. Recommended defaults below.

### Recommended defaults (British)

| Speaker | Voice ID | Grade |
|---------|----------|-------|
| Female | `bf_emma` | **B-** (highest-rated female voice) |
| Male | `bm_george` | C (best available British male; try blends below) |

### Other British options
| Voice ID | Grade | Notes |
|----------|-------|-------|
| `bf_isabella` | C | Alternate female |
| `bm_fable` | C | Alternate male |
| `bm_lewis` | D+ | Deeper timbre |

### Other accents (US female, US male, etc.)

See [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) for the full list. Common picks: `af_heart` (A grade, US female), `af_bella`, `am_michael`.

### Voice blending

Kokoro supports blending two or more voices in a single request — pass a comma-separated list and the underlying `KokoroPipeline.load_voice` averages the style tensors (`torch.mean(torch.stack(packs), dim=0)`). Useful when a single grade-C voice has artefacts you can smooth out by averaging with a sibling.

```bash
# Blend two British males
curl "http://localhost:8969/v1/audio/speech" -s \
  -H "Content-Type: application/json" --output blend.mp3 \
  --data '{
    "input": "The forecast is bright with occasional showers.",
    "model": "speaches-ai/Kokoro-82M-v1.0-ONNX",
    "voice": "bm_george,bm_fable"
  }'
```

Blending is supported by any Kokoro-based server that calls the native `kokoro` package (Speaches, `mlx-audio`, `kokoro-fastapi`). Weighted blends (`voice1:60,voice2:40`) are a `kokoro-tts` / `kokoro-fastapi` extension — the plain `,` form averages 50/50 and works everywhere.

### Test different voices

```bash
for voice in bf_emma bm_george "bm_george,bm_fable" "bf_emma,bf_isabella"; do
  fn=$(echo "$voice" | tr "," "+")
  curl "http://localhost:8969/v1/audio/speech" -s \
    -H "Content-Type: application/json" \
    --output "test_${fn}.mp3" \
    --data "{
      \"input\": \"Hello, this is the ${voice} voice.\",
      \"model\": \"speaches-ai/Kokoro-82M-v1.0-ONNX\",
      \"voice\": \"${voice}\"
    }"
done
```

---

## GPU Acceleration

For faster generation with NVIDIA GPUs:

```yaml
services:
  speaches:
    image: ghcr.io/speaches-ai/speaches:latest-cuda
    container_name: speaches
    ports:
      - "8969:8000"
    volumes:
      - hf-hub-cache:/home/ubuntu/.cache/huggingface/hub
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  hf-hub-cache:
```

---

## Docker Networking

When configuring your OpenAI-Compatible credential in **Settings → API Keys**, use the appropriate TTS base URL for your setup:

### Open Notebook in Docker (macOS/Windows)

**TTS Base URL:** `http://host.docker.internal:8969/v1`

### Open Notebook in Docker (Linux)

**TTS Base URL (Option 1 — Docker bridge IP):** `http://172.17.0.1:8969/v1`

**Option 2:** Use host networking mode (`docker run --network host ...`), then use: `http://localhost:8969/v1`

### Remote Server

Run Speaches on a different machine:

**TTS Base URL:** `http://server-ip:8969/v1` (replace with your server's IP)

---

## Multi-Speaker Podcasts

Configure different voices for each speaker (British defaults shown, blends optional):

```
Speaker 1 (Host):
  Model: speaches-ai/Kokoro-82M-v1.0-ONNX
  Voice: bf_emma

Speaker 2 (Guest):
  Model: speaches-ai/Kokoro-82M-v1.0-ONNX
  Voice: bm_george

Speaker 3 (Narrator, blend example):
  Model: speaches-ai/Kokoro-82M-v1.0-ONNX
  Voice: bf_emma,bf_isabella
```

The `voice_id` field in `speakers_config.json` (podcast-creator) accepts the same comma-blend syntax — no code change needed to swap presets or try new blends.

---

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker compose logs speaches

# Verify port available
lsof -i :8969

# Restart
docker compose down && docker compose up -d
```

### Connection Refused

```bash
# Test Speaches is running
curl http://localhost:8969/v1/models

# From inside Open Notebook container
docker exec -it open-notebook curl http://host.docker.internal:8969/v1/models
```

### Model Not Found

```bash
# List downloaded models
docker compose exec speaches uv tool run speaches-cli model list

# Download if missing
docker compose exec speaches uv tool run speaches-cli model download speaches-ai/Kokoro-82M-v1.0-ONNX
```

### Poor Audio Quality

- Try different voices
- Adjust speed: `"speed": 0.9` to `1.2`
- Check model downloaded completely
- Allocate more memory

### Slow Generation

| Solution | How |
|----------|-----|
| Use GPU | Switch to `latest-cuda` image |
| More CPU | Allocate more cores in Docker |
| Faster model | Use smaller/quantized models |
| SSD storage | Move Docker volumes to SSD |

---

## Performance Tips

### Recommended Specs

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 2 GB | 4+ GB |
| Storage | 5 GB | 10 GB (for multiple models) |
| GPU | None | NVIDIA (optional) |

### Resource Limits

```yaml
services:
  speaches:
    # ... other config
    mem_limit: 4g
    cpus: 2
```

### Monitor Usage

```bash
docker stats speaches
```

---

## Comparison: Local vs Cloud

| Aspect | Local (Speaches) | Cloud (OpenAI/ElevenLabs) |
|--------|------------------|---------------------------|
| **Cost** | Free | $0.015-0.10/min |
| **Privacy** | Complete | Data sent to provider |
| **Speed** | Depends on hardware | Usually faster |
| **Quality** | Good | Excellent |
| **Setup** | Moderate | Simple API key |
| **Offline** | Yes | No |
| **Voices** | Limited | Many options |

### When to Use Local

- Privacy-sensitive content
- High-volume generation
- Development/testing
- Offline environments
- Cost control

### When to Use Cloud

- Premium quality needs
- Multiple languages
- Time-sensitive projects
- Limited hardware

---

## Other Local TTS Options

Any OpenAI-compatible TTS server works. The key is:

1. Server implements `/v1/audio/speech` endpoint
2. Add an OpenAI-Compatible credential in **Settings → API Keys** with the TTS base URL
3. Add model with provider `openai_compatible`

---

## Related

- **[Local STT Setup](local-stt.md)** - Speech-to-text with Speaches
- **[OpenAI-Compatible Providers](openai-compatible.md)** - General compatible provider setup
- **[AI Providers](ai-providers.md)** - All provider configuration
- **[Creating Podcasts](../3-USER-GUIDE/creating-podcasts.md)** - Using TTS for podcasts
