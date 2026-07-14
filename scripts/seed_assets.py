"""Synthesize royalty-free-by-construction SFX + ambient music loops."""
import math, random, struct, wave, sys
from pathlib import Path

SR = 44100

def write(path, samples):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(struct.pack(f"<{len(samples)}h",
            *(max(-32767, min(32767, int(s * 32767))) for s in samples)))

def whoosh(dur=0.35):
    n = int(SR * dur); random.seed(7)
    out, lp = [], 0.0
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 2
        cutoff = 0.04 + 0.5 * math.sin(math.pi * t)  # sweep
        lp += cutoff * (random.uniform(-1, 1) - lp)
        out.append(lp * env * 0.7)
    return out

def pop(dur=0.09):
    n = int(SR * dur)
    return [math.sin(2 * math.pi * (900 - 500 * (i / n)) * i / SR)
            * math.exp(-i / (SR * 0.018)) * 0.8 for i in range(n)]

def lofi_loop(dur=16.0, root=110.0, name="warm"):
    n = int(SR * dur); out = [0.0] * n
    chords = {  # minor-7-ish pads
        "warm": [(1, 1.2, 1.5, 1.782), (0.8909, 1.0691, 1.335, 1.6)],
        "night": [(1, 1.189, 1.498, 1.782), (0.9439, 1.122, 1.414, 1.682)],
    }[name]
    bar = n // len(chords)
    for ci, chord in enumerate(chords):
        for ratio in chord:
            f = root * ratio
            ph = random.random() * 6.28
            for i in range(bar):
                j = ci * bar + i
                if j >= n: break
                t = i / SR
                env = min(t / 1.5, 1.0, (bar / SR - t) / 1.5)
                s = (math.sin(2 * math.pi * f * t + ph)
                     + 0.4 * math.sin(2 * math.pi * f * 2.005 * t)
                     + 0.2 * math.sin(2 * math.pi * f * 0.5 * t))
                out[j] += s * env * 0.06
    # gentle vinyl noise texture — kept low relative to the chord partials
    # (each partial is amplitude 0.06 above) so it reads as a subtle analog
    # texture, not broadband hiss drowning out the actual pad.
    random.seed(3); lp = 0.0
    for j in range(n):
        lp += 0.02 * (random.uniform(-1, 1) - lp)
        out[j] += lp * 0.02
    return out

sfx_dir, music_dir = Path(sys.argv[1]), Path(sys.argv[2])
write(sfx_dir / "whoosh.wav", whoosh())
write(sfx_dir / "pop.wav", pop())
write(music_dir / "lofi-warm.wav", lofi_loop(name="warm"))
write(music_dir / "lofi-night.wav", lofi_loop(name="night", root=98.0))
print("seeded", sfx_dir, music_dir)
