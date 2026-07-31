"""Cross-correlate rx/tx energy envelopes to find the echo delay."""
import wave, sys, numpy as np

WIN_MS = 5

def load(p):
    with wave.open(p, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64), w.getframerate()

def env(x, sr, win_ms=WIN_MS):
    w = max(1, int(sr * win_ms / 1000))
    n = len(x) // w
    return np.abs(x[:n * w].reshape(n, w)).mean(axis=1)

def bar(e, cols=90):
    step = max(1, len(e) // cols)
    ch = [e[i:i + step].max() for i in range(0, len(e), step)][:cols]
    pk = max(ch) or 1
    g = " .:-=+*#%@"
    return "".join(g[min(9, int(c / pk * 9))] for c in ch)

rx, sr = load("/tmp/lat-in.wav")
tx, _  = load("/tmp/lat-out.wav")

a, b = env(rx, sr), env(tx, sr)
n = min(len(a), len(b)); a, b = a[:n], b[:n]

N = 1 << int(np.ceil(np.log2(2 * n)))
corr = np.fft.irfft(np.fft.rfft(b - b.mean(), N) * np.conj(np.fft.rfft(a - a.mean(), N)), N)

max_lag = int(2000 / WIN_MS)          # search up to 2s
lag = int(np.argmax(corr[:max_lag]))
conf = corr[lag] / (np.abs(corr[:max_lag]).max() or 1)

print(f"duration    : {len(rx)/sr:.1f}s @ {sr} Hz")
print(f"\nRX (you)  |{bar(a)}|")
print(f"TX (echo) |{bar(b)}|")
print(f"\nconfidence  : {conf:.2f}   (1.00 = clean single peak)")
print(f"\n>>> ROUND-TRIP LATENCY: {lag*WIN_MS} ms   (Asterisk -> LiveKit -> agent -> back)\n")

if conf < 0.9:
    print("!! Weak correlation - clap once, clearly, with silence around it.\n")
