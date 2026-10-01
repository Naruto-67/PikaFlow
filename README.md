# 🌸 PikaFlow — Animerise Channel Automation

> **AI‑powered anime‑style YouTube video pipeline, running entirely on free‑tier services via GitHub Actions.**

---

## What is PikaFlow?

PikaFlow is a modern, zero‑cost video‑automation system that:

- Generates **full anime‑style video clips** (via CPU‑only Stable Video Diffusion / AnimateDiff)
- Adds **AI narration** (Bark TTS), **background music**, **kinetic captions**, and **lower‑thirds**
- Edits the timeline automatically (CapCut‑style AI editing via FFmpeg)
- Uploads the final video to the **Animerise** YouTube channel
- Runs 100% on **GitHub Actions** — no paid cloud infra required

---

## Architecture

```
Dev Panel (GitHub Pages, owner‑only token) 
    └─► GitHub Repository Dispatch
            └─► Init Job (LLM selection, spec generation)
                    └─► Video Generation Jobs (chunked)
                            └─► Video Editor Job
                                    └─► GitHub Release (run-ddmmyyyy-hhmm)
                                            └─► YouTube Upload (optional)
```

---

## Repository Structure

```
PikaFlow/
├── .github/
│   └── workflows/
│       ├── pika_flow.yml         # Main pipeline workflow
│       └── discovery.yml         # Nightly LLM discovery & health‑check
├── pika_flow/
│   ├── __init__.py
│   ├── orchestrator.py           # Main pipeline orchestrator
│   ├── llm_manager.py            # Dynamic LLM manager (free‑tier only)
│   ├── video_generator.py        # Clip generation (video diffusion)
│   ├── video_editor.py           # AI timeline editor (FFmpeg‑Python)
│   ├── diffusion_wrapper.py      # Image/video diffusion wrapper
│   ├── audio_generator.py        # TTS + background music
│   ├── logger.py                 # JSON‑Lines logger + Markdown summary
│   ├── release_manager.py        # GitHub Release creation & upload
│   └── utils/
│       ├── __init__.py
│       ├── connection_manager.py # HTTP client with retry & circuit‑breaker
│       └── schema_validator.py   # JSON‑Schema config validation
├── config/
│   ├── channel_config.json       # Channel / niche settings
│   ├── llm_providers.json        # Auto‑generated free‑tier provider list
│   ├── quotas_state.json         # Per‑run quota tracker
│   ├── llm_performance.json      # Provider benchmark history
│   ├── video_editor.json         # Editor defaults (transitions, captions)
│   └── ui.json                   # Dev‑Panel UI settings
├── assets/
│   └── templates/
│       ├── intro.png
│       ├── outro.png
│       └── lower_third.svg
├── tests/
│   ├── test_connection_manager.py
│   ├── test_llm_manager.py
│   └── test_video_editor.py
├── docs/
│   └── architecture.md
├── requirements.txt
└── README.md
```

---

## Quick Start

1. **Fork this repo** and make it **Public** (required for free GitHub Actions minutes).
2. Add the following **GitHub Secrets**:
   | Secret | Description |
   |--------|-------------|
   | `GEMINI_API_KEY` | Google Gemini free‑tier API key |
   | `GROQ_API_KEY` | Groq free‑tier API key |
   | `OPENROUTER_API_KEY` | OpenRouter free‑tier API key |
   | `HUGGINGFACE_API_KEY` | HuggingFace Inference API key |
   | `DISCORD_WEBHOOK_URL` | (Optional) Discord alert webhook |
   | `YOUTUBE_API_KEY` | (Optional) YouTube Data API key for auto‑upload |
   | `DEV_PANEL_TOKEN` | Short‑lived owner‑only panel token |

3. Go to **Actions → PikaFlow Pipeline → Run workflow** and provide your video prompt.

---

## Configuration

All settings are in `config/` — **no hard‑coding anywhere in the codebase**.  
Edit `config/channel_config.json` to set your niche, aspect ratio, target platform, and more.

---

## Logging

Logs are written as **JSON‑Lines** (`log.jsonl`) plus a **Markdown summary** (`summary.md`).  
Both are attached to the GitHub Release for every run.

---

## Release Naming

Every run creates a GitHub Release tagged `run-ddmmyyyy-hhmm`  
(e.g., `run-01102026-1332` for 1 Oct 2026 at 13:32).

---

## License

MIT — see [LICENSE](LICENSE).

