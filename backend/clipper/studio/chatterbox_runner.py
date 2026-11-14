"""Speak sentences with Chatterbox Turbo. Run by `chatterbox.speak` with the Python of its own environment.

Standalone on purpose: it imports nothing from the app, because it runs in a
separate virtual environment (chatterbox-tts pins torch, numpy and transformers
versions the app does not use). Its memory is freed when it exits.

    python chatterbox_runner.py job.json

job.json: {"sentences": ["[dramatic] text", ...], "reference": "voice.wav" or null,
           "out": "folder", "threads": 4}
Writes out/000.wav, out/001.wav... (24 kHz mono) and prints "PROGRESS done total" lines.
"""

import json
import sys
from pathlib import Path


def main(job_path: str) -> None:
    job = json.loads(Path(job_path).read_text())
    import torch
    import torchaudio
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    torch.set_num_threads(max(1, int(job.get("threads") or 4)))
    model = ChatterboxTurboTTS.from_pretrained(device="cpu")      # Apple's GPU backend was slower in tests
    if job.get("reference"):
        model.prepare_conditionals(job["reference"])
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    total = len(job["sentences"])
    print(f"PROGRESS 0 {total}", flush=True)
    for n, text in enumerate(job["sentences"]):
        target = out / f"{n:03d}.wav"
        if not target.is_file():                                   # a cancelled take resumes where it stopped
            with torch.inference_mode():
                wav = model.generate(text)
            torchaudio.save(str(target.with_suffix(".part.wav")), wav.cpu(), model.sr,
                            encoding="PCM_S", bits_per_sample=16)
            target.with_suffix(".part.wav").replace(target)
        print(f"PROGRESS {n + 1} {total}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
