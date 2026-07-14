# recap

Auto-generate music-synced recap videos from phone footage.

Drop a folder of video clips and a music track — get a Kdenlive project with clips cut to the beat, using each clip's most exciting segment.

## Install

```bash
pip install -e .
```

Requires Python ≥3.10, ffmpeg, and [Kdenlive](https://kdenlive.org/) (for rendering).

## Quick start

```bash
recap create ~/Videos/my-clips/ ~/Music/track.wav -o recap.kdenlive
```

Then open in Kdenlive and render:

```bash
kdenlive recap.kdenlive
```

## Pipeline

`recap create` runs five stages:

1. **Transcode** — converts VFR phone footage to constant 25fps (optional, `--no-transcode` to skip)
2. **Analyze** — scores each clip for visual motion, finds the most exciting segment
3. **Assign** — maps clips to beat slots on the music timeline (shuffled tiers by default)
4. **Render** — writes a `.kdenlive` project with beat-aligned in/out points

## Individual commands

```bash
# Analyze a single video
recap analyze ~/Videos/clip.mp4

# Detect beats in a music file
recap beats ~/Music/track.wav

# Assign clips to beats (outputs plan JSON)
recap assign --clips ~/Videos/my-clips/ --music ~/Music/track.wav

# Generate kdenlive project from a plan
recap render --plan plan.json --music ~/Music/track.wav -o recap.kdenlive
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | `shuffled-tiers` | Assignment strategy: `shuffled-tiers` or `best-match` |
| `--min-beats` | `4` | Minimum beats per clip |
| `--max-beats` | `8` | Maximum beats per clip |
| `--ratio` | `16:9` | Output aspect ratio: `16:9` or `9:16` |
| `--fps` | `25` | Timeline frame rate |
| `--seed` | `42` | Random seed for reproducibility |
| `--force` | off | Re-analyze everything, ignore caches |
| `--no-transcode` | off | Skip VFR→CFR transcode step |

## Requirements

- Python 3.10+
- ffmpeg (for video probing and transcoding)
- Kdenlive 23.04+ (for rendering the generated project)
- essentia (for beat detection)
- librosa (for audio energy analysis)
- OpenCV (for video motion analysis)
