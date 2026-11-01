"""
Batur Teacher Talking
=====================
Always-on window that shows what is being said, word by word, in real time.

Two engines — switch any time in the top bar:
  * Online (Gemini) — speech recognition in Google's cloud. Works on any PC,
    no GPU, almost no CPU. Needs a Gemini API key (api_key.txt).
  * Local (Whisper) — runs on this PC's NVIDIA GPU (or CPU). Free, offline.

Audio source (🎤 menu): any microphone, or "Computer sound" (whatever the PC
plays — Zoom through headphones or speakers).

Start: Batur Teacher Talking.exe   Stop: close window / Esc   Log: teacher_talking.log
"""

import os, sys, json, glob, queue, threading, time, re, subprocess, traceback, base64, asyncio

FROZEN = getattr(sys, "frozen", False)            # running as the built .exe / .app
IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
if FROZEN:
    APP_DIR = os.path.dirname(sys.executable)       # folder holding the .exe
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)     # bundled resources (icon)
    if IS_MAC:
        DATA_DIR = os.path.expanduser("~/Library/Application Support/BaturTeacherTalking")
    else:
        DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), "BaturTeacherTalking")
else:
    APP_DIR = RES_DIR = DATA_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
LOG_PATH = os.path.join(DATA_DIR, "teacher_talking.log")

# pythonw has no console: route prints/warnings to a log file.
if sys.stdout is None or sys.stderr is None or "pythonw" in sys.executable.lower():
    _log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

# GPU libraries (cuBLAS/cuDNN) for Local mode: reuse any existing copy.
GPU_DIR_CANDIDATES = [
    os.path.join(APP_DIR, "_gpu_libs"),
    os.path.join(os.path.dirname(APP_DIR), "TeacherTalking", "_gpu_libs"),
    os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "BaturTeacherTalking", "_gpu_libs"),
]
GPU_DIR = next((d for d in GPU_DIR_CANDIDATES
                if glob.glob(os.path.join(d, "nvidia", "cublas", "bin", "*.dll"))),
               GPU_DIR_CANDIDATES[-1])


def _register_gpu_dlls():
    for d in glob.glob(os.path.join(GPU_DIR, "nvidia", "*", "bin")):
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(d)
        except Exception:
            pass


_register_gpu_dlls()
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "5")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "20")

import numpy as np
import tkinter as tk
from tkinter import simpledialog, messagebox

# ── Settings ──────────────────────────────────────────────────────────────────
SAMPLE_RATE    = 16_000
FRAME_SEC      = 0.03          # VAD frame
SILENCE_END    = 0.50          # pause that ends a sentence (s)
PREROLL_SEC    = 0.30          # audio kept from just before speech starts
BACKLOG_SEC    = 25.0          # audio kept while an engine is still loading

# Local (Whisper)
LIVE_STEP_SEC  = 0.12
MAX_PHRASE_SEC = 12.0
MIN_PHRASE_SEC = 0.45
CPU_MODELS = ("base", "small.en")
GPU_MODELS = ("small.en", "medium")
LANGUAGE   = "en"

# Online (Gemini)
MODEL       = "models/gemini-3.5-transcribe-live"
WS_URL      = ("wss://generativelanguage.googleapis.com/ws/"
               "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
LANGUAGES   = ["en-US"]
SEND_SEC    = 0.10
RENEW_AFTER = 8.5 * 60
RENEW_FORCE = 9.6 * 60

LOOPBACK = "__loopback__"      # audio-source value meaning "computer sound"

FONT_FAMILY = "Segoe UI" if os.name == "nt" else ("Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans")
FONT_SIZE   = 22
BG_COLOR    = "#ffffff"
TEXT_COLOR  = "#111111"
LIVE_COLOR  = "#8c8c8c"
DOT_ON      = "#e8232a"
DOT_OFF     = "#c08080"

SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
KEY_PATHS = [os.path.join(DATA_DIR, "api_key.txt"),
             os.path.join(APP_DIR, "api_key.txt"),
             os.path.join(APP_DIR, "gemini api key.txt")]
# ─────────────────────────────────────────────────────────────────────────────

FRAME = int(SAMPLE_RATE * FRAME_SEC)

HALLUCINATIONS = {
    "thanks for watching", "thank you for watching", "thanks for watching bye",
    "please subscribe", "subtitles by the amaraorg community", "you", "bye", "",
}


def log(*a):
    try:
        print(time.strftime("%H:%M:%S"), *a, flush=True)
    except Exception:
        pass


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception:
        pass


def load_api_key():
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if k:
        return k
    for p in KEY_PATHS:
        try:
            with open(p, encoding="utf-8-sig") as f:
                k = f.read().strip()
            if k:
                return k
        except Exception:
            pass
    return ""


def save_api_key(k):
    with open(KEY_PATHS[0], "w", encoding="utf-8") as f:
        f.write(k.strip())


def com_init():
    """Each thread that touches Windows audio (WASAPI) must initialise COM."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass


def pip_install(pkgs, target=None):
    """Install packages silently (no console window)."""
    if FROZEN:
        return False                                  # the .exe already bundles everything
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        alt = py[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            py = alt
    cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location"]
    if target:
        cmd += ["--target", target]
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    r = subprocess.run(cmd + list(pkgs), stdout=sys.stdout, stderr=sys.stdout, creationflags=flags)
    return r.returncode == 0


def _norm(t):
    return re.sub(r"[^\w\s]", "", t.lower()).strip()


def collapse_loops(text):
    """Remove Whisper repetition loops ("okay okay okay…", "X? X? X?")."""
    w = text.split()
    changed = True
    while changed:
        changed = False
        for n in range(1, 7):
            i = 0
            while i + 2 * n <= len(w):
                a = [_norm(x) for x in w[i:i + n]]
                j = i + n
                reps = 0
                while j + n <= len(w) and [_norm(x) for x in w[j:j + n]] == a:
                    j += n
                    reps += 1
                # allow one natural repeat ("very very"), cut loops of 3+
                if reps >= 2 or (reps == 1 and n >= 3):
                    w = w[:i + n] + w[j:]
                    changed = True
                i += 1
    # trailing partial repetition of the first words ("X. Y. X")
    return " ".join(w)


def clean_text(text):
    text = collapse_loops(" ".join(text.split()))
    if _norm(text) in HALLUCINATIONS:
        return ""
    return text




def clean_simple(t):
    return " ".join((t or "").split())


# ─── Audio capture ───────────────────────────────────────────────────────────

class AudioCapture:
    """Delivers 30 ms float32 mono 16 kHz frames to `out_q` from the chosen source."""

    def __init__(self, out_q, on_status):
        self.out_q = out_q
        self.on_status = on_status
        self.source = ""                  # "" = default mic, LOOPBACK, or mic name
        self._max_q = int(BACKLOG_SEC / FRAME_SEC)
        self.active_name = ""
        self._gen = 0                     # bumps on every restart; old threads exit
        self._running = False
        self._sd = None
        self._sc = None
        self._lock = threading.Lock()

    def _put(self, frame):
        """Queue a frame; while an engine is still loading keep only the last BACKLOG_SEC."""
        q = self.out_q
        while q.qsize() > self._max_q:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        q.put(frame)

    # modules (imported lazily; soundcard auto-installed if missing)
    def sd(self):
        if self._sd is None:
            import sounddevice
            self._sd = sounddevice
        return self._sd

    def sc(self):
        if self._sc is None:
            try:
                import soundcard
            except ImportError:
                self.on_status("Installing audio component (one time)…")
                log("installing soundcard")
                pip_install(["soundcard>=0.4.4"])
                import soundcard
            self._sc = soundcard
        return self._sc

    def start(self):
        self._running = True
        self.restart()
        threading.Thread(target=self._watch_default_output, daemon=True).start()

    def stop(self):
        self._running = False
        self._gen += 1

    def set_source(self, src):
        self.source = src
        self.restart()

    def restart(self):
        with self._lock:
            self._gen += 1
            gen = self._gen
        target = self._run_loopback if self.source == LOOPBACK else self._run_mic
        threading.Thread(target=target, args=(gen,), daemon=True).start()

    def list_mics(self):
        sd = self.sd()
        try:
            sd._terminate(); sd._initialize()      # pick up newly plugged mics
        except Exception:
            pass
        try:
            devs = sd.query_devices()
            din = sd.default.device[0]
            api = devs[din]["hostapi"] if din is not None and din >= 0 else sd.default.hostapi
            dname = devs[din]["name"] if din is not None and din >= 0 else ""
        except Exception as e:
            log("device list failed:", repr(e))
            return [], ""
        names = []
        for d in devs:
            n = d["name"]
            if d["max_input_channels"] > 0 and d["hostapi"] == api and n not in names \
                    and "mapper" not in n.lower() and "primary sound capture" not in n.lower():
                names.append(n)
        return names, dname

    def default_output_name(self):
        com_init()
        try:
            return self.sc().default_speaker().name
        except Exception:
            return ""

    # computer sound (WASAPI loopback of the current default output device)
    def _run_loopback(self, gen):
        if not IS_WIN:
            self.active_name = "Computer sound (not available on this system)"
            self.on_status("Computer sound needs Windows. On Mac install BlackHole and pick it as the microphone.")
            return
        com_init()
        while self._running and gen == self._gen:
            try:
                sc = self.sc()
                spk = sc.default_speaker()
                dev = sc.get_microphone(id=str(spk.name), include_loopback=True)
                self.active_name = f"Computer sound ({spk.name})"
                self._spk_id = spk.id
                log("source:", self.active_name)
                self.on_status("SOURCE")
                with dev.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME) as rec:
                    while self._running and gen == self._gen:
                        data = rec.record(numframes=FRAME)
                        self._put(np.ascontiguousarray(data[:, 0], dtype=np.float32))
                return
            except Exception as e:
                if gen != self._gen:
                    return
                log("loopback error:", traceback.format_exc())
                self.on_status(f"Computer-sound capture error: {e} — retrying…")
                time.sleep(2)

    def _watch_default_output(self):
        """Headphones plugged in / default output changed → follow it."""
        com_init()
        last = None
        while self._running:
            time.sleep(2.0)
            if self.source != LOOPBACK or self._sc is None or not IS_WIN:
                continue
            try:
                cur = self._sc.default_speaker().id
            except Exception:
                continue
            if last is not None and cur != last:
                log("default output changed → restarting loopback")
                self.restart()
            last = cur

    # microphone (PortAudio)
    def _run_mic(self, gen):
        sd = self.sd()
        while self._running and gen == self._gen:
            try:
                dev = None
                if self.source:
                    devs = sd.query_devices()
                    hits = [i for i, d in enumerate(devs)
                            if d["name"] == self.source and d["max_input_channels"] > 0]
                    if hits:
                        dev = hits[0]
                    else:
                        self.on_status(f"'{self.source}' not found — using default microphone")
                info = sd.query_devices(dev if dev is not None else sd.default.device[0])
                stream = self._open_mic(sd, dev, info)
                self.active_name = info["name"]
                log("source:", self.active_name)
                self.on_status("SOURCE")
                with stream:
                    while self._running and gen == self._gen:
                        time.sleep(0.05)
                return
            except Exception as e:
                if gen != self._gen:
                    return
                log("mic error:", repr(e))
                self.on_status(f"Microphone error: {e} — retrying…")
                time.sleep(1.5)

    def _open_mic(self, sd, dev, info):
        def cb16(indata, frames, _t, status):
            self._put(indata[:, 0].copy())
        try:
            return sd.InputStream(device=dev, samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                  blocksize=FRAME, latency="low", callback=cb16)
        except Exception as e:
            log("16 kHz not supported, resampling:", repr(e))
        sr = int(info.get("default_samplerate") or 48000)
        ratio = SAMPLE_RATE / sr

        def cb(indata, frames, _t, status):
            x = indata[:, 0]
            n = max(1, int(round(len(x) * ratio)))
            self._put(np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
                           .astype(np.float32))
        return sd.InputStream(device=dev, samplerate=sr, channels=1, dtype="float32",
                              blocksize=int(sr * FRAME_SEC), latency="low", callback=cb)


# ─── Local model handling ────────────────────────────────────────────────────

def has_nvidia_driver():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.exists(os.path.join(root, "System32", "nvcuda.dll"))


def _cpu_threads():
    n = os.cpu_count() or 4
    return max(2, n // 2)


FALLBACKS = ["small.en", "base.en", "base", "small", "tiny.en", "tiny", "medium.en", "medium"]


def open_model(name, device, compute_type, **kw):
    """Prefer models already on disk; download only as a last resort."""
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(name, device=device, compute_type=compute_type,
                            local_files_only=True, **kw), name
    except Exception:
        pass
    for alt in FALLBACKS:                       # any cached model will do
        if alt == name:
            continue
        try:
            m = WhisperModel(alt, device=device, compute_type=compute_type,
                             local_files_only=True, **kw)
            log(f"'{name}' not on disk — using cached '{alt}' instead")
            return m, alt
        except Exception:
            continue
    log(f"downloading '{name}'…")
    return WhisperModel(name, device=device, compute_type=compute_type, **kw), name


def load_model(name, device):
    """Load one Whisper model on device ('cuda' or 'cpu'), warm it up."""
    if device == "cuda":
        last = None
        for ct in ("int8_float16", "float16", "int8", "float32"):
            try:
                m, real = open_model(name, "cuda", ct)
                _warmup(m)
                log(f"loaded {real} cuda {ct}")
                return m
            except Exception as e:
                last = e
        raise last
    m, real = open_model(name, "cpu", "int8", cpu_threads=_cpu_threads(), num_workers=1)
    _warmup(m)
    log(f"loaded {real} cpu int8")
    return m


_MODEL_CACHE = {}      # (name, device) -> model ; survives engine switches


def get_model(name, device):
    key = (name, device)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = load_model(name, device)
    return _MODEL_CACHE[key]


def _warmup(model):
    z = np.zeros(SAMPLE_RATE, dtype=np.float32)
    segs, _ = model.transcribe(z, beam_size=1, language="en",
                               without_timestamps=True, vad_filter=False)
    list(segs)


def gpu_libs_ready():
    return bool(glob.glob(os.path.join(GPU_DIR, "nvidia", "cublas", "bin", "cublas64_*.dll"))) and \
           bool(glob.glob(os.path.join(GPU_DIR, "nvidia", "cudnn", "bin", "cudnn_ops64_9.dll")))


def install_gpu_libs(on_status):
    """One-time silent download of cuBLAS + cuDNN wheels into ./_gpu_libs."""
    on_status("NVIDIA GPU found — downloading CUDA libraries in background (one time)…")
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        alt = py[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            py = alt
    cmd = [py, "-m", "pip", "install", "--disable-pip-version-check",
           "--no-warn-script-location", "--target", GPU_DIR,
           "nvidia-cublas-cu12==12.9.1.4", "nvidia-cudnn-cu12==9.10.2.21"]
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    os.makedirs(GPU_DIR, exist_ok=True)
    r = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stdout,
                       creationflags=flags)
    _register_gpu_dlls()
    return r.returncode == 0 and gpu_libs_ready()


# ─── Local engine (Whisper on this PC) ───────────────────────────────────────

class LocalEngine:
    """
    on_live(pid, text) / on_final(pid, text) / on_status(msg)
    Starts listening as soon as the fast 'live' model is ready; the accurate
    'final' model loads in the background (live model is used until then).
    """
    title = "Local"

    def __init__(self, audio_q, on_live, on_final, on_status):
        self.audio_q = audio_q
        self.on_live, self.on_final, self.on_status = on_live, on_final, on_status
        self.language = LANGUAGE
        self._final_q = queue.Queue()
        self._running = False
        self._models_lock = threading.Lock()
        self.live_model = self.final_model = None
        self.device = "cpu"
        self.ready = False
        self._committed_tail = ""
        self.level = -90.0

    def label(self):
        return f"Local · {'GPU' if self.device == 'cuda' else 'CPU'}"

    def start(self):
        self._running = True
        threading.Thread(target=self._boot, daemon=True).start()

    def stop(self):
        self._running = False

    def _boot(self):
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            self.on_status("ERROR:Local mode isn't installed on this PC — use Online")
            return
        try:
            device = "cuda" if has_nvidia_driver() and gpu_libs_ready() else "cpu"
            names = GPU_MODELS if device == "cuda" else CPU_MODELS
            self.on_status(f"LOADING:Loading speech model on {'GPU' if device == 'cuda' else 'CPU'}…")
            try:
                live = get_model(names[0], device)
            except Exception as e:
                log("GPU load failed, using CPU:", repr(e))
                device, names = "cpu", CPU_MODELS
                live = get_model(names[0], device)
            final = _MODEL_CACHE.get((names[1], device))
            with self._models_lock:
                self.device = device
                self.live_model = live
                self.final_model = final or live
        except Exception as exc:
            log(traceback.format_exc())
            self.on_status(f"ERROR:Could not load the speech model: {exc}")
            return
        if not self._running:
            return
        threading.Thread(target=self._final_loop, daemon=True).start()
        if final is None:
            threading.Thread(target=self._load_final, args=(names[1], device), daemon=True).start()
        if device == "cpu" and has_nvidia_driver() and not gpu_libs_ready():
            threading.Thread(target=self._upgrade_to_gpu, daemon=True).start()
        self.ready = True
        self.on_status("READY")
        self._live_loop()

    def _load_final(self, name, device):
        try:
            m = get_model(name, device)
            with self._models_lock:
                if self.device == device:
                    self.final_model = m
            log("final model ready:", name)
        except Exception:
            log("final model load failed (live model keeps working):\n" + traceback.format_exc())

    def _upgrade_to_gpu(self):
        try:
            if not install_gpu_libs(lambda m: log(m)):
                log("GPU lib install failed — staying on CPU")
                return
            live = get_model(GPU_MODELS[0], "cuda")
            final = get_model(GPU_MODELS[1], "cuda")
            with self._models_lock:
                self.live_model, self.final_model, self.device = live, final, "cuda"
            log("hot-switched to GPU")
            self.on_status("READY")
        except Exception:
            log("GPU upgrade failed:\n" + traceback.format_exc())

    # decoding helpers ------------------------------------------------------
    def _decode(self, model, audio, beam, lang, prompt):
        """Accurate decode (full 30 s Whisper window) — used for final text."""
        segs, info = model.transcribe(
            audio,
            language=lang,
            beam_size=beam,
            best_of=1,
            temperature=0.0,
            without_timestamps=True,
            condition_on_previous_text=False,
            vad_filter=False,
            initial_prompt=prompt or None,
            suppress_blank=True,
            no_speech_threshold=0.6,
        )
        parts = []
        for s in segs:
            if s.no_speech_prob > 0.75 and s.avg_logprob < -0.8:
                continue
            parts.append(s.text)
        return clean_text("".join(parts)), info.language, info.language_probability

    def _fast_decode(self, model, audio, lang, prompt):
        """
        Low-latency decode for live text. Whisper normally pads every input to
        a 30 s window, so the encoder costs the same for 1 s of speech as for
        30 s. Here the encoder only sees the real audio (+1 s margin) — the
        same trick as whisper.cpp's audio_ctx — which is 5-10x faster.
        """
        import ctranslate2
        from faster_whisper.tokenizer import Tokenizer
        feats = model.feature_extractor(audio)
        n = feats.shape[-1]
        ctx = min(3000, int(np.ceil((n + 100) / 100.0)) * 100)
        if n < ctx:
            feats = np.pad(feats, ((0, 0), (0, ctx - n)))
        else:
            feats = feats[:, :3000]
        enc = model.model.encode(
            ctranslate2.StorageView.from_array(np.ascontiguousarray(feats[None], dtype=np.float32)),
            to_cpu=False)
        prob = 1.0
        if lang is None:
            if model.model.is_multilingual:
                tok, prob = model.model.detect_language(enc)[0][0]
                lang = tok[2:-2]
            else:
                lang = "en"
        tk_ = Tokenizer(model.hf_tokenizer, model.model.is_multilingual,
                        task="transcribe", language=lang)
        tokens = []
        if prompt:
            tokens = [tk_.sot_prev] + tk_.encode(" " + prompt.strip())[-64:]
        tokens += list(tk_.sot_sequence) + [tk_.no_timestamps]
        sec = len(audio) / SAMPLE_RATE
        res = model.model.generate(
            enc, [tokens], beam_size=1,
            max_length=min(448, len(tokens) + int(8 * sec) + 6),
            repetition_penalty=1.05,
            suppress_blank=True, suppress_tokens=[-1],
            return_no_speech_prob=True,
        )[0]
        if res.no_speech_prob > 0.8:
            return "", lang, prob
        ids = [t for t in res.sequences_ids[0] if t < tk_.eot]
        return clean_text(tk_.decode(ids)), lang, prob

    def _lang_arg(self, phrase_lang):
        return self.language

    # live loop: VAD + provisional decoding --------------------------------
    def _live_loop(self):
        noise_db = -55.0
        pre = []                      # preroll frames
        pre_max = int(PREROLL_SEC / FRAME_SEC)
        phrase = []                   # list of frames
        phrase_dbs = []
        in_speech = False
        speech_run = 0
        silence_run = 0
        pid = 0
        decoded_len = 0
        phrase_lang = None
        leftover = np.zeros(0, dtype=np.float32)

        while self._running:
            # 1) drain everything the mic produced since last decode
            got = []
            try:
                got.append(self.audio_q.get(timeout=0.05))
                while True:
                    got.append(self.audio_q.get_nowait())
            except queue.Empty:
                pass
            if got:
                buf = np.concatenate([leftover] + got)
                n = len(buf) // FRAME
                leftover = buf[n * FRAME:]
                for i in range(n):
                    fr = buf[i * FRAME:(i + 1) * FRAME]
                    db = 20 * np.log10(np.sqrt(np.mean(fr * fr)) + 1e-9)
                    self.level = db
                    thr = max(noise_db + 9.0, -48.0)
                    is_sp = db > thr
                    if not in_speech:
                        # adaptive noise floor (only while not speaking)
                        noise_db = (0.97 * noise_db + 0.03 * db) if db < noise_db + 6 else noise_db + 0.02
                        noise_db = min(max(noise_db, -75.0), -30.0)
                        pre.append(fr)
                        if len(pre) > pre_max:
                            pre.pop(0)
                        speech_run = speech_run + 1 if is_sp else 0
                        if speech_run >= 3:                     # 90 ms of voice
                            in_speech = True
                            phrase = list(pre)
                            phrase_dbs = [db] * len(phrase)
                            pre = []
                            silence_run = 0
                            decoded_len = 0
                            pid += 1
                            phrase_lang = None
                    else:
                        phrase.append(fr)
                        phrase_dbs.append(db)
                        silence_run = 0 if is_sp else silence_run + 1
                        if silence_run * FRAME_SEC >= SILENCE_END:
                            self._close_phrase(pid, phrase, phrase_lang)
                            in_speech = False
                            speech_run = 0
                            phrase, phrase_dbs = [], []
                        elif len(phrase) * FRAME_SEC >= MAX_PHRASE_SEC:
                            # cut at the quietest frame in the last 5 s
                            lo = max(1, len(phrase) - int(5.0 / FRAME_SEC))
                            hi = len(phrase) - int(0.5 / FRAME_SEC)
                            cut = lo + int(np.argmin(phrase_dbs[lo:hi])) if hi > lo else len(phrase)
                            head = phrase[:cut]
                            self._refresh_live(pid, head, phrase_lang)
                            self._close_phrase(pid, head, phrase_lang)
                            phrase, phrase_dbs = phrase[cut:], phrase_dbs[cut:]
                            pid += 1
                            decoded_len = 0
                            silence_run = 0

            # 2) provisional decode of the phrase-so-far
            if in_speech and (len(phrase) - decoded_len) * FRAME_SEC >= LIVE_STEP_SEC \
                    and len(phrase) * FRAME_SEC >= MIN_PHRASE_SEC:
                decoded_len = len(phrase)
                audio = np.concatenate(phrase)
                with self._models_lock:
                    model = self.live_model
                try:
                    text, _, _ = self._fast_decode(
                        model, audio, self.language, self._committed_tail)
                    self.on_live(pid, text)
                except Exception:
                    log(traceback.format_exc())

    def _refresh_live(self, pid, frames, lang):
        """Re-decode exactly the audio being finalized so the grey text
        doesn't overlap with the next phrase while the final pass runs."""
        try:
            with self._models_lock:
                model = self.live_model
            text, _, _ = self._fast_decode(model, np.concatenate(frames),
                                           self._lang_arg(lang), self._committed_tail)
            self.on_live(pid, text)
        except Exception:
            log(traceback.format_exc())

    def _close_phrase(self, pid, frames, lang):
        if len(frames) * FRAME_SEC < MIN_PHRASE_SEC + PREROLL_SEC:
            self.on_final(pid, "")
            return
        self._final_q.put((pid, np.concatenate(frames), lang))

    # final loop: accurate pass, in order -----------------------------------
    def _final_loop(self):
        while self._running:
            try:
                pid, audio, lang = self._final_q.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._models_lock:
                model = self.final_model
            try:
                text, _, _ = self._decode(model, audio, 3, self._lang_arg(lang),
                                          self._committed_tail)
            except Exception:
                log(traceback.format_exc())
                text = ""
            if text and len(audio) < 1.6 * SAMPLE_RATE and \
                    _norm(self._committed_tail).endswith(_norm(text)):
                log(f"dropped echo #{pid}: {text}")
                text = ""
            if text:
                self._committed_tail = (self._committed_tail + " " + text)[-200:]
            log(f"phrase #{pid} ({len(audio)/SAMPLE_RATE:.1f}s): {text[:80]}")
            self.on_final(pid, text)


# ─── Online engine (Gemini) ──────────────────────────────────────────────────

class GeminiEngine:
    """
    on_live(pid, text)  – grey interim text for sentence pid (replaces previous)
    on_final(pid, text) – final text for sentence pid
    on_status(msg)      – "READY", "SOURCE", "KEY_ERROR" or a message
    """

    title = "Online"

    def __init__(self, audio_q, api_key, on_live, on_final, on_status):
        self.api_key = api_key
        self.on_live, self.on_final, self.on_status = on_live, on_final, on_status
        self.audio_q = audio_q
        self.level = -90.0
        self.connected = False
        self._running = False
        self._pid = 1
        self._awaiting_final = False
        self._renew = False
        self._ever_connected = False

    @property
    def ready(self):
        return self.connected

    def label(self):
        return "Online · Gemini"

    def start(self):
        self._running = True
        threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True).start()

    def stop(self):
        self._running = False

    def set_key(self, key):
        self.api_key = key
        self._renew = True

    # session loop ----------------------------------------------------------
    async def _main(self):
        try:
            import websockets
        except ImportError:
            self.on_status("LOADING:Installing online component (one time)…")
            ok = await asyncio.to_thread(pip_install, ["websockets>=12"])
            try:
                import websockets
            except ImportError:
                self.on_status("ERROR:Could not install the online component (websockets)")
                return
        backoff = 1.0
        while self._running:
            if not self.api_key:
                self.on_status("KEY_MISSING")
                await asyncio.sleep(1.0)
                continue
            if not self._ever_connected:
                self.on_status("LOADING:Connecting to Gemini…")
            try:
                async with websockets.connect(f"{WS_URL}?key={self.api_key}", max_size=None,
                                              open_timeout=15, ping_interval=20,
                                              ping_timeout=20) as ws:
                    await ws.send(json.dumps({"setup": {
                        "model": MODEL,
                        "generationConfig": {"responseModalities": ["TEXT"]},
                        "inputAudioTranscription": {"languageCodes": LANGUAGES},
                    }}))
                    first = json.loads(await asyncio.wait_for(ws.recv(), 15))
                    if "setupComplete" not in first:
                        raise RuntimeError(f"setup failed: {str(first)[:300]}")
                    log("gemini session open")
                    self.connected, self._renew, backoff = True, False, 1.0
                    self._ever_connected = True
                    self.on_status("READY")
                    tasks = [asyncio.create_task(self._send_loop(ws, time.time())),
                             asyncio.create_task(self._recv_loop(ws))]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        t.result()
                    log("session renewed")
            except Exception as e:
                self.connected = False
                msg = str(e)
                log("gemini connection:", repr(e))
                if any(s in msg for s in ("API key", "API_KEY", "1007", "1008", "PERMISSION", "403", "401")):
                    self.on_status("KEY_ERROR")
                    log(traceback.format_exc())
                    await asyncio.sleep(3)
                else:
                    self.on_status(f"LOADING:Reconnecting to Gemini… ({msg[:60]})")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
            self.connected = False

    async def _send_loop(self, ws, started):
        noise_db = -60.0
        pre, pre_max = [], int(PREROLL_SEC / FRAME_SEC)
        in_speech, speech_run, silence = False, 0, 0.0
        out, out_n = [], 0
        send_n = int(SAMPLE_RATE * SEND_SEC)
        last_frame_t = time.time()
        final_wait_since = 0.0

        async def send_audio(chunks):
            pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2").tobytes()
            await ws.send(json.dumps({"realtimeInput": {"audio": {
                "data": base64.b64encode(pcm).decode(), "mimeType": "audio/pcm;rate=16000"}}}))

        async def end_turn():
            nonlocal in_speech, out, out_n, final_wait_since
            if out:
                await send_audio(out)
            out, out_n = [], 0
            await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
            in_speech = False
            self._awaiting_final = True
            final_wait_since = time.time()

        while self._running:
            frames = []
            try:
                while True:
                    frames.append(self.audio_q.get_nowait())
            except queue.Empty:
                pass
            now = time.time()
            if frames:
                last_frame_t = now
            for fr in frames:
                db = 20 * np.log10(np.sqrt(np.mean(fr * fr)) + 1e-9)
                self.level = db
                is_sp = db > max(noise_db + 9.0, -50.0)
                if not in_speech:
                    noise_db = (0.97 * noise_db + 0.03 * db) if db < noise_db + 6 else noise_db + 0.02
                    noise_db = min(max(noise_db, -80.0), -30.0)
                    pre.append(fr)
                    if len(pre) > pre_max:
                        pre.pop(0)
                    speech_run = speech_run + 1 if is_sp else 0
                    if speech_run >= 3:                       # 90 ms of voice → start
                        in_speech, silence = True, 0.0
                        out, out_n = list(pre), sum(len(x) for x in pre)
                        pre = []
                else:
                    out.append(fr)
                    out_n += len(fr)
                    silence = 0.0 if is_sp else silence + FRAME_SEC
                    if out_n >= send_n:
                        await send_audio(out)
                        out, out_n = [], 0
                    if silence >= SILENCE_END:
                        await end_turn()
            # source went quiet without delivering frames (e.g. device switch)
            if in_speech and now - last_frame_t > SILENCE_END + 0.2:
                await end_turn()
            if self._awaiting_final and now - final_wait_since > 4.0:
                self._awaiting_final = False
            # session renewal: only between sentences
            age = now - started
            if not in_speech and not self._awaiting_final and (self._renew or age > RENEW_AFTER):
                return
            if age > RENEW_FORCE:
                if in_speech:
                    await end_turn()
                    await asyncio.sleep(1.0)
                return
            await asyncio.sleep(0.02)

    async def _recv_loop(self, ws):
        async for raw in ws:
            try:
                m = json.loads(raw)
            except Exception:
                continue
            sc = m.get("serverContent") or {}
            it = sc.get("interimInputTranscription")
            if it is not None:
                self.on_live(self._pid, clean_simple(it.get("text", "")))
            ft = sc.get("inputTranscription")
            if ft is not None:
                text = clean_simple(ft.get("text", ""))
                self.on_final(self._pid, text)
                if text:
                    log(f"#{self._pid}: {text[:90]}")
                self._pid += 1
                self._awaiting_final = False
            if "goAway" in m:
                log("server goAway:", m["goAway"])
                self._renew = True


# ─── GUI ─────────────────────────────────────────────────────────────────────

PILL = {
    "loading": ("#fef3c7", "#92400e"),
    "ready":   ("#dcfce7", "#166534"),
    "error":   ("#fee2e2", "#991b1b"),
}


class TeacherTalkingApp:

    def __init__(self):
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title("Batur Teacher Talking")
        try:
            self.root.iconbitmap(default=os.path.join(RES_DIR, "app.ico"))
        except Exception:
            try:
                self._icon_img = tk.PhotoImage(file=os.path.join(RES_DIR, "icon.png"))
                self.root.iconphoto(True, self._icon_img)
            except Exception:
                pass
        self.root.configure(bg=BG_COLOR)
        self.root.geometry(self.settings.get("geometry", "1200x720+80+80"))
        self.root.attributes("-topmost", True)

        self._word_count = 0
        self._ui_q = queue.Queue()
        self._pending = {}            # pid -> grey text (ordered)
        self._last_final = 0
        self._fs = False
        self._key_prompted = False
        self._gen = 0
        self.engine = None
        self.mode = self.settings.get("mode", "online")

        self._build_ui()
        if self.mode == "local" and not self.local_available:
            self.mode = "online"

        # audio starts immediately, so nothing said during loading is lost
        self.audio_q = queue.Queue()
        self.capture = AudioCapture(self.audio_q,
                                    on_status=lambda m: self._ui_q.put(("status", None, m, None)))
        self.capture.source = self.settings.get("source", "")
        if self.capture.source == LOOPBACK and not IS_WIN:
            self.capture.source = ""
        self._update_src_label()
        self.capture.start()
        self._start_engine(self.mode)

        self.root.bind("<Escape>", lambda e: self._quit())
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self._poll()
        self._pulse()

    # ── engines ───────────────────────────────────────────────────────────
    def _start_engine(self, mode):
        if self.engine is not None:
            self.engine.stop()
        self._gen += 1
        g = self._gen
        cb = dict(on_live=lambda p, t: self._ui_q.put(("live", g, p, t)),
                  on_final=lambda p, t: self._ui_q.put(("final", g, p, t)),
                  on_status=lambda m: self._ui_q.put(("status", g, m, None)))
        if mode == "local":
            self.engine = LocalEngine(self.audio_q, **cb)
        else:
            self.engine = GeminiEngine(self.audio_q, load_api_key(), **cb)
        self.mode = mode
        self._pending.clear()
        self._last_final = 0
        self._render_tail()
        self._paint_mode()
        self._set_state("loading", "Loading — please wait" if mode == "local"
                        else "Connecting — please wait")
        self.engine.start()
        log("engine:", mode)

    def _switch_mode(self, mode):
        if mode == self.mode:
            return
        self.settings["mode"] = mode
        save_settings(self.settings)
        self._start_engine(mode)

    # ── UI construction ───────────────────────────────────────────────────
    def _build_ui(self):
        top = tk.Frame(self.root, bg="#f7f7f7", height=48)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)

        self._dot = tk.Label(top, text="●", font=(FONT_FAMILY, 16), bg="#f7f7f7", fg=DOT_ON)
        self._dot.pack(side="left", padx=(14, 6), pady=10)
        tk.Label(top, text="Batur Teacher Talking", font=(FONT_FAMILY, 13, "bold"),
                 bg="#f7f7f7", fg="#222").pack(side="left", pady=10)
        self._pill = tk.Label(top, text="", font=(FONT_FAMILY, 10, "bold"), padx=12, pady=3)
        self._pill.pack(side="left", padx=14, pady=10)

        btn = dict(font=(FONT_FAMILY, 10), bg="#e2e2e2", relief="flat", padx=12, pady=4)
        for lbl, cmd in [("Clear", self._clear), ("Copy", self._copy)]:
            tk.Button(top, text=lbl, command=cmd, **btn).pack(side="right", padx=6, pady=8)
        tk.Button(top, text="Fullscreen", command=self._toggle_fs, **btn
                  ).pack(side="right", padx=0, pady=8)

        # audio-source picker
        self._src_var = tk.StringVar(value="")
        self._src_btn = tk.Menubutton(top, text="🎤 Source ▾", font=(FONT_FAMILY, 10),
                                      bg="#e2e2e2", activebackground="#d4d4d4",
                                      relief="flat", padx=12, pady=4)
        self._src_menu = tk.Menu(self._src_btn, tearoff=0, font=(FONT_FAMILY, 10),
                                 postcommand=self._fill_src_menu)
        self._src_btn.configure(menu=self._src_menu)
        self._src_btn.pack(side="right", padx=6, pady=8)

        # Local / Online switch
        sw = tk.Frame(top, bg="#cfcfcf", padx=1, pady=1)
        sw.pack(side="right", padx=8, pady=9)
        self._mode_btns = {}
        for key, label in (("local", "💻 Local"), ("online", "☁ Online")):
            b = tk.Button(sw, text=label, font=(FONT_FAMILY, 10, "bold"), relief="flat",
                          bd=0, padx=12, pady=3, command=lambda k=key: self._switch_mode(k))
            b.pack(side="left")
            self._mode_btns[key] = b
        self.local_available = self._local_available()

        frame = tk.Frame(self.root, bg=BG_COLOR)
        frame.pack(fill="both", expand=True, padx=36, pady=(20, 10))

        self._text = tk.Text(
            frame, wrap="word", font=(FONT_FAMILY, FONT_SIZE),
            fg=TEXT_COLOR, bg=BG_COLOR, relief="flat", bd=0,
            highlightthickness=0, padx=6, pady=6,
            insertbackground=BG_COLOR, cursor="arrow",
            spacing2=6, spacing3=10,
        )
        self._text.pack(fill="both", expand=True)
        self._text.tag_configure("live", foreground=LIVE_COLOR)
        # [committed lines] tail [grey live lines] pad \n [spacer]
        # Spacer = half the window, so scrolling to the bottom keeps the newest
        # line in the vertical centre.
        self._spacer = tk.Frame(self._text, bg=BG_COLOR, width=1, height=300)
        self._text.insert("1.0", "\n")
        self._text.window_create("end-1c", window=self._spacer)
        self._text.mark_set("tail", "1.0")
        self._text.mark_gravity("tail", "left")
        self._text.mark_set("pad", "1.0")
        self._text.mark_gravity("pad", "right")
        self._text.bind("<Configure>", self._on_resize)
        self._text.configure(state="disabled")

        # big hint shown while the page is empty
        self._hint = tk.Label(frame, text="", font=(FONT_FAMILY, 26), fg="#b5b5b5", bg=BG_COLOR)

        sb = tk.Scrollbar(frame, command=self._text.yview, width=6, bg="#ccc")
        sb.place(relx=1.0, rely=0, relheight=1, anchor="ne")
        self._text.configure(yscrollcommand=sb.set)

        self._status_var = tk.StringVar(value="Starting…")
        tk.Label(self.root, textvariable=self._status_var, font=(FONT_FAMILY, 9),
                 bg="#f7f7f7", fg="#888", anchor="w"
                 ).pack(fill="x", side="bottom", padx=16, pady=(2, 6))

    @staticmethod
    def _local_available():
        if FROZEN:
            return False
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None

    def _paint_mode(self):
        for k, b in self._mode_btns.items():
            if k == "local" and not self.local_available:
                b.configure(state="disabled", bg="#f3f3f3", fg="#bbb", text="💻 Local (n/a)")
                continue
            on = (k == self.mode)
            b.configure(bg="#2563eb" if on else "#f3f3f3", fg="white" if on else "#444",
                        activebackground="#1d4ed8" if on else "#e5e5e5",
                        activeforeground="white" if on else "#222")

    def _set_state(self, state, text):
        self._state = state
        bg, fg = PILL[state]
        icon = {"loading": "⏳", "ready": "●", "error": "⚠"}[state]
        self._pill.configure(text=f"{icon}  {text}", bg=bg, fg=fg)
        self._update_hint()

    def _update_hint(self):
        empty = not self._text.compare("pad", ">", "1.0")
        if not empty:
            self._hint.place_forget()
            return
        msg = {"loading": "Loading… please wait",
               "ready": "Ready — start talking",
               "error": self._pill.cget("text")[3:]}[self._state]
        self._hint.configure(text=msg, fg="#2f9e55" if self._state == "ready" else "#b5b5b5")
        self._hint.place(relx=0.5, rely=0.42, anchor="center")

    # ── text rendering ────────────────────────────────────────────────────
    def _has_text_before_tail(self):
        return self._text.compare("tail", ">", "1.0")

    def _on_resize(self, ev):
        h = max(40, ev.height // 2 - FONT_SIZE)
        if abs(int(self._spacer.cget("height")) - h) > 2:
            self._spacer.configure(height=h)
            self._scroll_center()

    def _scroll_center(self):
        self._text.yview_moveto(1.0)

    def _render_tail(self):
        t = self._text
        t.configure(state="normal")
        t.delete("tail", "pad")
        live = "\n".join(x for x in self._pending.values() if x)
        if live:
            sep = "\n" if self._has_text_before_tail() else ""
            t.insert("tail", sep + live, ("live",))
        t.configure(state="disabled")
        self._scroll_center()

    def _commit(self, text):
        t = self._text
        t.configure(state="normal")
        sep = "\n" if self._has_text_before_tail() else ""
        t.insert("tail", sep + text)
        t.mark_set("tail", f"tail + {len(sep + text)}c")
        t.configure(state="disabled")
        self._word_count += len(text.split())

    # ── queue pump ────────────────────────────────────────────────────────
    def _poll(self):
        changed = False
        try:
            while True:
                kind, g, a, b = self._ui_q.get_nowait()
                if g is not None and g != self._gen:
                    continue                      # message from an engine we switched away from
                if kind == "live":
                    if a > self._last_final:
                        self._pending[a] = b
                        changed = True
                elif kind == "final":
                    for k in [k for k in self._pending if k < a]:
                        self._pending.pop(k)
                    self._pending.pop(a, None)
                    self._last_final = max(self._last_final, a)
                    if b:
                        self._commit(b)
                    changed = True
                else:
                    self._on_status(a)
        except queue.Empty:
            pass
        if changed:
            self._render_tail()
            self._update_hint()
            self._status_var.set(self._status_line())
        self.root.after(16, self._poll)

    def _on_status(self, m):
        if m == "READY":
            self._set_state("ready", "RECORDING")
            self._status_var.set(self._status_line())
        elif m == "SOURCE":
            self._status_var.set(self._status_line())
        elif m.startswith("LOADING:"):
            self._set_state("loading", "Loading — please wait")
            self._status_var.set(m[8:])
        elif m.startswith("ERROR:"):
            self._set_state("error", m[6:])
            self._status_var.set(m[6:])
        elif m == "KEY_MISSING":
            self._set_state("error", "Gemini API key needed")
            if not self._key_prompted:
                self._key_prompted = True
                self.root.after(200, self._ask_key)
        elif m == "KEY_ERROR":
            self._set_state("error", "Gemini rejected the API key")
            if not self._key_prompted:
                self._key_prompted = True
                self.root.after(200, lambda: self._ask_key(
                    "Gemini rejected this API key.\nPaste a valid Gemini API key:"))
        else:
            self._status_var.set(m)

    def _status_line(self):
        src = self.capture.active_name or "…"
        return f"{self.engine.label()}  |  Source: {src}  |  {self._word_count} words  |  Esc to exit"

    # ── buttons ───────────────────────────────────────────────────────────
    def _clear(self):
        t = self._text
        t.configure(state="normal")
        t.delete("1.0", "pad")
        t.mark_set("tail", "1.0")
        t.mark_set("pad", "1.0")
        t.configure(state="disabled")
        self._pending.clear()
        self._word_count = 0
        self._update_hint()
        self._status_var.set("Cleared — still listening…")

    def _copy(self):
        content = self._text.get("1.0", "pad").strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._status_var.set("Copied! — still listening…")

    def _toggle_fs(self):
        self._fs = not self._fs
        self.root.attributes("-fullscreen", self._fs)

    def _pulse(self):
        lvl = self.engine.level if self.engine else -90
        self._dot.configure(fg=DOT_ON if lvl > -45 else DOT_OFF)
        self.root.after(80, self._pulse)

    # ── audio-source picker ───────────────────────────────────────────────
    def _fill_src_menu(self):
        m = self._src_menu
        m.delete(0, "end")
        cap = self.capture
        self._src_var.set(cap.source)
        names, dname = cap.list_mics()
        m.add_radiobutton(label=f"🎤  Default microphone  ({dname or 'system'})",
                          variable=self._src_var, value="",
                          command=lambda: self._choose_src(""))
        for n in names:
            m.add_radiobutton(label=f"🎤  {n}", variable=self._src_var, value=n,
                              command=lambda n=n: self._choose_src(n))
        if cap.source not in ("", LOOPBACK) and cap.source not in names:
            m.add_radiobutton(label=f"🎤  {cap.source}  (not connected)",
                              variable=self._src_var, value=cap.source, state="disabled")
        m.add_separator()
        if IS_WIN:
            out = cap.default_output_name() if cap._sc is not None else ""
            m.add_radiobutton(label=f"🔊  Computer sound — Zoom / headphones{f'  ({out})' if out else ''}",
                              variable=self._src_var, value=LOOPBACK,
                              command=lambda: self._choose_src(LOOPBACK))
        else:
            m.add_command(label="🔊  Computer sound on Mac: install BlackHole, then pick it above",
                          state="disabled")
        m.add_separator()
        m.add_command(label="🔑  Set Gemini API key…", command=self._ask_key)

    def _choose_src(self, src):
        self.capture.set_source(src)
        self.settings["source"] = src
        save_settings(self.settings)
        self._update_src_label()
        self._status_var.set("Switching audio source…")

    def _update_src_label(self):
        src = self.capture.source
        if src == LOOPBACK:
            txt = "🔊 Computer sound"
        else:
            n = src or "Default mic"
            txt = "🎤 " + (n[:24] + "…" if len(n) > 25 else n)
        self._src_btn.configure(text=txt + " ▾")

    def _ask_key(self, prompt="Paste your Gemini API key:"):
        k = simpledialog.askstring("Gemini API key", prompt, parent=self.root, show="•")
        if k and k.strip():
            save_api_key(k)
            self._key_prompted = False
            if isinstance(self.engine, GeminiEngine):
                self.engine.set_key(k.strip())
                self._set_state("loading", "Connecting — please wait")
        elif isinstance(self.engine, GeminiEngine) and not self.engine.api_key:
            messagebox.showwarning("Batur Teacher Talking",
                                   "Online mode needs a Gemini API key.\n"
                                   "Add it via 🎤 Source ▸ Set Gemini API key — or switch to Local.",
                                   parent=self.root)

    def _quit(self):
        try:
            if not self._fs:
                self.settings["geometry"] = self.root.geometry()
            save_settings(self.settings)
        except Exception:
            pass
        try:
            self.engine.stop()
            self.capture.stop()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


# ─── Entry point ─────────────────────────────────────────────────────────────

def selftest():
    """Used by the GitHub build: verify every bundled component loads."""
    out = os.environ.get("BTT_SELFTEST_OUT") or os.path.join(os.environ.get("TEMP", DATA_DIR), "btt_selftest.txt")
    lines, ok = [], True
    for mod in ("numpy", "sounddevice", "websockets", "tkinter") + (("soundcard",) if IS_WIN else ()):
        try:
            __import__(mod)
            lines.append(f"ok   {mod}")
        except Exception as e:
            ok = False
            lines.append(f"FAIL {mod}: {e!r}")
    try:
        r = tk.Tk(); r.withdraw(); r.update(); r.destroy()
        lines.append("ok   tk window")
    except Exception as e:
        lines.append(f"warn tk window: {e!r}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "BaturTeacherTalkingSingleton")
        if ctypes.windll.kernel32.GetLastError() == 183:
            sys.exit(0)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Batur.TeacherTalking")
    except Exception:
        pass
    log("=== start", sys.executable)
    TeacherTalkingApp().run()
