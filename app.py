import base64
import datetime
import html
import io
import json
import os
import random
import re
import threading
import time
import wave

import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types
from PIL import Image, ImageOps

from header import render_header

# ---------- ตั้งค่าการจำกัดการใช้งาน (แก้ตัวเลขได้) ----------
MAX_PER_SESSION = 10   # จำนวนครั้งต่อการเปิดหน้าเว็บหนึ่งครั้ง
MAX_PER_DAY = 200      # จำนวนครั้งรวมทุกคนต่อวัน (กันโควตา Gemini หมด)
HOTLINE = "1556"       # สายด่วน อย.

# ลิงก์เอกสารอ้างอิง (ปลายทางของ QR Code ท้ายหน้า) ใส่ URL จริงตรงนี้
# ถ้าเว้นว่างไว้ จะไม่แสดงปุ่มลิงก์
REFERENCE_URL = ""

# ---------- ตั้งค่าเสียงน้องกระเทียม (แก้ตัวเลขได้) ----------
# เสียงหลัก: Gemini TTS (ลื่นไหล เป็นธรรมชาติ ใช้ API key เดียวกัน)
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Leda"     # เสียงสดใส ลองเปลี่ยนได้: Zephyr, Kore, Puck, Aoede
MAX_TTS_PER_SESSION = 5

# เสียงสำรอง (เสียงในเครื่องผู้ใช้ ใช้เมื่อ Gemini TTS ใช้ไม่ได้)
VOICE_RATE = 1.0       # ความเร็วปกติ (0.9 จะรู้สึกช้าและติดๆ)
VOICE_PITCH = 1.0      # ระดับเสียงปกติ (สูงมากจะฟังเพี้ยน)

# รุ่นโมเดล: ตัวแรกคือรุ่นหลัก ตัวถัดไปคือสำรอง (ถ้า 404 หรือโควตาหมดจะข้ามไปตัวถัดไป)
# (ตามหน้า Models ของ Google อัปเดต 13 ต.ค. 2026: รุ่น 2.5 จำกัดให้เฉพาะผู้ที่เคยใช้ และ
#  แนะนำให้โปรเจกต์ใหม่ใช้ 3.8 Flash / 3.5 Flash-Lite จึงย้ายรุ่น 2.5 ไปเป็นตัวสำรองท้ายสุด)
MODELS = [
    "gemini-3.8-flash",       # หลัก: รุ่นใหม่สุด เสถียร
    "gemini-3.6-flash",       # สำรอง 1
    "gemini-3.5-flash-lite",  # สำรอง 2: เร็ว ประหยัด
    "gemini-3.1-flash-lite",  # สำรอง 3
    "gemini-2.5-flash",       # สำรองท้ายสุด (รุ่นเก่า)
]

# ---------- ตั้งค่าการรอเมื่อ Google ยุ่ง (แก้ตัวเลขได้) ----------
MAX_WAIT_SECONDS = 25     # รอรวมสูงสุดกี่วินาทีก่อนยอมแพ้ (ไม่รอนานเกินนี้)
ATTEMPTS_PER_MODEL = 3    # ลองซ้ำกี่ครั้งต่อรุ่นเมื่อ Google ยุ่ง
REQUEST_TIMEOUT_MS = 20000  # เวลารอสูงสุดต่อการเรียกหนึ่งครั้ง (มิลลิวินาที)


def get_api_key():
    """อ่านรหัส API จาก environment (Codespaces secret) หรือ .streamlit/secrets.toml
    ห้ามเขียนรหัสลงในไฟล์นี้โดยตรง"""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return None


@st.cache_resource
def _daily_counter():
    return {"date": None, "count": 0, "lock": threading.Lock()}


def try_use_quota():
    """คืนค่า None ถ้าใช้ได้ หรือข้อความบอกเหตุผลที่ใช้ไม่ได้"""
    today = datetime.date.today().isoformat()
    c = _daily_counter()
    with c["lock"]:
        if c["date"] != today:
            c["date"], c["count"] = today, 0
        if c["count"] >= MAX_PER_DAY:
            return "วันนี้มีคนใช้ครบจำนวนที่กำหนดแล้ว กรุณาลองใหม่พรุ่งนี้ครับ"
        if st.session_state.get("used", 0) >= MAX_PER_SESSION:
            return f"ใช้ครบ {MAX_PER_SESSION} ครั้งสำหรับการเปิดหน้านี้แล้ว กรุณาลองใหม่ภายหลังครับ"
        c["count"] += 1
        st.session_state["used"] = st.session_state.get("used", 0) + 1
    return None


def refund_quota():
    """คืนสิทธิ์เมื่อระบบ AI ล้มเหลว (ไม่ใช่ความผิดของผู้ใช้)"""
    c = _daily_counter()
    with c["lock"]:
        c["count"] = max(0, c["count"] - 1)
        st.session_state["used"] = max(0, st.session_state.get("used", 0) - 1)


def speak_button(text: str):
    """ปุ่มอ่านผลออกเสียงภาษาไทย ใช้เสียงในเครื่องของผู้ใช้ (ไม่ใช้โควตา)"""
    t = json.dumps(text, ensure_ascii=False).replace("</", "<\\/")
    components.html(
        f"""
        <button id="b" style="font-family:sans-serif;font-size:18px;font-weight:700;
          padding:10px 18px;border:2px solid #2f6b45;border-radius:10px;
          background:#fff;color:#1d2a21;cursor:pointer">ฟังผลการตรวจ</button>
        <script>
          const t = {t};
          function pickThaiVoice() {{
            const voices = window.speechSynthesis.getVoices().filter(v => v.lang.startsWith('th'));
            return voices.find(v => /premwadee|female|google/i.test(v.name)) || voices[0] || null;
          }}
          document.getElementById('b').onclick = () => {{
            window.speechSynthesis.cancel();
            const u = new SpeechSynthesisUtterance(t);
            u.lang = 'th-TH';
            u.rate = {VOICE_RATE};
            u.pitch = {VOICE_PITCH};
            const v = pickThaiVoice();
            if (v) u.voice = v;
            window.speechSynthesis.speak(u);
          }};
        </script>
        """,
        height=64,
    )


def clean_for_speech(text: str) -> str:
    """ตัดส่วนที่เสียงสังเคราะห์อ่านเพี้ยน เช่น ชื่อภาษาอังกฤษในวงเล็บ"""
    text = re.sub(r"\([A-Za-z0-9 \-\.]+\)", "", text)
    text = text.replace("อย.", "อย").replace("/", " หรือ ")
    return re.sub(r"\s+", " ", text).strip()


def synthesize_speech(client, text: str) -> bytes:
    """สร้างเสียงพูดภาษาไทยด้วย Gemini TTS คืนค่าเป็นไฟล์ WAV (bytes)"""
    prompt = (
        "พูดด้วยน้ำเสียงเด็กน้อยใจดี สดใส เป็นกันเอง ความเร็วปกติ "
        "พูดต่อเนื่องลื่นไหลเหมือนคนไทยพูดจริง ไม่ติดขัด: " + text[:700]
    )
    resp = client.models.generate_content(
        model=TTS_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
                )
            ),
        ),
    )
    pcm = resp.candidates[0].content.parts[0].inline_data.data
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)       # 16-bit
        w.setframerate(24000)   # Gemini TTS ส่งมาที่ 24 kHz
        w.writeframes(pcm)
    return buf.getvalue()


def voice_section(speech: str):
    """ปุ่มฟังผล: ใช้ Gemini TTS ก่อน ถ้าไม่ได้ให้ใช้เสียงในเครื่องแทน"""
    cache = st.session_state.get("tts_cache")
    clicked = st.button("🔊 ฟังผลการตรวจ (น้องกระเทียมอ่านให้ฟัง)", key="tts_btn")
    if clicked and not (cache and cache["text"] == speech):
        if st.session_state.get("tts_used", 0) >= MAX_TTS_PER_SESSION:
            st.caption("ใช้เสียงน้องกระเทียมครบจำนวนแล้ว ใช้เสียงสำรองแทน")
            speak_button(speech)
            return
        try:
            with st.spinner("น้องกระเทียมกำลังเตรียมเสียง..."):
                wav = synthesize_speech(make_client(get_api_key()), speech)
            st.session_state["tts_used"] = st.session_state.get("tts_used", 0) + 1
            cache = {"text": speech, "wav": wav}
            st.session_state["tts_cache"] = cache
        except Exception:
            st.caption("ตอนนี้เสียงหลักใช้ไม่ได้ ใช้เสียงสำรองในเครื่องแทน")
            speak_button(speech)
            return
    if cache and cache["text"] == speech:
        try:
            st.audio(cache["wav"], format="audio/wav", autoplay=clicked)
        except TypeError:  # Streamlit รุ่นเก่าไม่มี autoplay
            st.audio(cache["wav"], format="audio/wav")


def show_ai_result(text: str):
    """แสดงผลเป็นแถบสี: เขียว = ผ่าน, แดง = ไม่ผ่าน, เหลือง = ภาพไม่ชัด
    คืนค่า (สถานะ, ข้อความสำหรับอ่านออกเสียง)"""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return "warn", ""
    first = lines[0]
    if "✅" in first and "❌" not in first:
        status = "ok"
    elif "❌" in first:
        status = "fail"
    else:
        status = "warn"
    title = html.escape(
        first.replace("✅", "").replace("❌", "").replace("⚠️", "").replace("⚠", "").strip()
    )

    def row(line):
        line = html.escape(line)
        if ":" in line[:40]:
            label, rest = line.split(":", 1)
            return f"<b style='display:inline'>{label}:</b>{rest}"
        return line

    main = [l for l in lines[1:] if not l.startswith("จุดที่")]
    issues = [l for l in lines[1:] if l.startswith("จุดที่")]
    body = "<br>".join(row(l) for l in main)
    st.markdown(
        f'<div class="res {status}"><b>{title}</b>{body}</div>',
        unsafe_allow_html=True,
    )
    for l in issues:
        label, _, rest = html.escape(l).partition(":")
        st.markdown(
            f'<div class="res fail"><b>{label}</b>{rest.strip()}</div>',
            unsafe_allow_html=True,
        )

    speech = ". ".join(
        l.replace("✅", "").replace("❌", "").replace("⚠️", "").replace("⚠", "").strip()
        for l in lines
    )
    return status, clean_for_speech(speech)


def make_client(api_key):
    """สร้าง client พร้อมกำหนดเวลารอสูงสุดต่อครั้ง กันค้างนาน"""
    try:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
    except Exception:
        return genai.Client(api_key=api_key)


def _err_kind(e) -> str:
    """แยกชนิดข้อผิดพลาด: quota (429) / busy (5xx) / notfound (404) / other"""
    msg = str(e)
    code = getattr(e, "code", None)
    if code == 429 or "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "quota"
    if code in (500, 502, 503, 504) or any(
        k in msg for k in ("503", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED", "timed out", "Timeout")
    ):
        return "busy"
    if code == 404 or "404" in msg or "NOT_FOUND" in msg:
        return "notfound"
    return "other"


def generate_with_retry(client, prompt, image):
    """เรียก Gemini โดยรอไม่เกิน MAX_WAIT_SECONDS รวม
    - Google ยุ่ง (503): รอแล้วลองซ้ำ (1, 2, 3 วินาที) ถ้ายังไม่ได้ไปรุ่นสำรอง
    - โควตารุ่นนั้นหมด (429) หรือไม่มีรุ่นนั้น (404): ข้ามไปรุ่นถัดไปทันที"""
    fast_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),  # ปิดการคิด = เร็วขึ้นมาก
        max_output_tokens=600,
        temperature=0.2,
    )
    deadline = time.time() + MAX_WAIT_SECONDS
    last_err = None
    for model in MODELS:
        config = fast_config
        for attempt in range(ATTEMPTS_PER_MODEL):
            if time.time() >= deadline:
                break
            try:
                return client.models.generate_content(
                    model=model, contents=[image, prompt], config=config
                )
            except Exception as e:
                last_err = e
                kind = _err_kind(e)
                print(f"[gemini-error] model={model} attempt={attempt + 1} kind={kind}: {e}", flush=True)
                if kind == "busy":
                    wait = (attempt + 1) + random.random() * 0.5
                    if time.time() + wait >= deadline:
                        break
                    time.sleep(wait)
                    continue
                if kind in ("quota", "notfound"):
                    break  # ไปรุ่นถัดไป
                if "thinking" in str(e).lower() and config is fast_config:
                    config = types.GenerateContentConfig(max_output_tokens=600, temperature=0.2)
                    continue
                raise
        if time.time() >= deadline:
            break
    if last_err is None:
        last_err = RuntimeError("503 UNAVAILABLE: timeout")
    raise last_err


def render_footer(path: str = "footer_banner.mp4"):
    """แสดงแบนเนอร์วิดีโอท้ายหน้า เล่นอัตโนมัติ วนซ้ำ ไม่มีเสียง"""
    full = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(full):
        return
    with open(full, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    st.markdown(
        f"""<video autoplay loop muted playsinline
        style="width:100%;border-radius:12px;margin-top:24px;display:block">
        <source src="data:video/mp4;base64,{b64}" type="video/mp4"></video>""",
        unsafe_allow_html=True,
    )


# ---------- หน้าเว็บ ----------
st.set_page_config(
    page_title="ฉลาดอ่านฉลาก (Smart Label Scanner)",
    page_icon="🧄",
    layout="centered",
)
render_header()

# ขนาดตัวอักษร (สำหรับผู้สูงอายุ)
size = st.radio(
    "ขนาดตัวอักษร", ["ปกติ", "ใหญ่", "ใหญ่มาก"], horizontal=True, key="font_size"
)
px = {"ปกติ": 19, "ใหญ่": 23, "ใหญ่มาก": 27}[size]
st.markdown(
    f"""<style>
    .stApp p, .stApp li, .stApp label, .stApp .res {{ font-size: {px}px !important; }}

    /* ข้อความตัวเล็ก (caption) ให้เข้มขึ้น อ่านง่าย */
    [data-testid="stCaptionContainer"] {{ color: #34423a !important; opacity: 1 !important; }}
    [data-testid="stCaptionContainer"] p {{ font-size: {round(px * 0.8)}px !important; color: #34423a !important; opacity: 1 !important; }}

    /* ข้อความใต้ปุ่มอัปโหลด (200MB ต่อไฟล์ • JPG, PNG) */
    [data-testid="stFileUploaderDropzoneInstructions"],
    [data-testid="stFileUploaderDropzoneInstructions"] span,
    [data-testid="stFileUploaderDropzoneInstructions"] small {{
        color: #34423a !important; opacity: 1 !important;
        font-size: {round(px * 0.8)}px !important;
    }}

    /* ช่องกรอกเลขทะเบียน: พื้นขาว ขอบเขียวเข้ม ไม่ให้ดูเหมือนถูกปิดใช้งาน */
    [data-testid="stTextInputRootElement"] {{
        border: 2px solid #2f6b45 !important; border-radius: 10px;
        background-color: #ffffff !important;
    }}
    [data-testid="stTextInputRootElement"] input {{
        background-color: #ffffff !important; color: #1d2a21 !important;
    }}
    [data-testid="stTextInputRootElement"]:focus-within {{
        box-shadow: 0 0 0 3px rgba(47, 107, 69, 0.25);
    }}

    /* แท็บที่เลือกอยู่: ตัวหนา มีพื้นอ่อนและเส้นใต้หนา */
    button[data-baseweb="tab"] {{ color: #34423a; }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        font-weight: 700; color: #1d2a21; background-color: #e4eae0;
        border-radius: 10px 10px 0 0;
    }}
    button[data-baseweb="tab"][aria-selected="true"] p {{ font-weight: 700 !important; }}
    [data-baseweb="tab-highlight"] {{ background-color: #2f6b45 !important; height: 4px !important; }}

    .res.warn {{ border-color: #c98a00; background: #fff7e0; }}
    </style>""",
    unsafe_allow_html=True,
)
st.write(
    "ตรวจฉลากผลิตภัณฑ์สมุนไพรจากรูปถ่าย เลขทะเบียน อย. และบาร์โค้ด "
    "ช่วยให้ประชาชนและ อสม. รู้ทันยาโอ้อวดได้ด้วยตัวเอง"
)
st.caption(
    "เป็นเครื่องมือคัดกรองเบื้องต้นจากข้อความและภาพที่ปรากฏบนฉลากเท่านั้น "
    "ไม่ได้ตรวจส่วนผสมจริงในผลิตภัณฑ์ และไม่ได้ยืนยันว่าผลิตภัณฑ์ได้รับอนุญาตจาก อย."
)

# 1. เลข อย.
st.subheader("1. ตรวจเลขทะเบียน อย.")
fda_number = st.text_input(
    "พิมพ์เลขทะเบียน อย. (เช่น G 123/2567)",
)
if fda_number:
    st.markdown(
        f"[ตรวจสอบเลข {html.escape(fda_number)} กับฐานข้อมูลของ อย. ที่นี่]"
        "(https://porta.fda.moph.go.th/fda_search_all/main/search_center_main.aspx)"
    )
    st.caption("แอปนี้ไม่ได้เชื่อมต่อฐานข้อมูลของ อย. โดยตรง กรุณากดลิงก์ด้านบนเพื่อยืนยันเลขทะเบียน")

# 2. รูปภาพ
st.subheader("2. ถ่ายหรืออัปโหลดรูปฉลาก")
st.caption(
    "ถ่ายในที่สว่าง ให้เห็นเลข อย. กรอบสี และคำเตือนบนฉลากชัดเจน ไม่เบลอ "
    "รองรับบาร์โค้ดและ QR Code"
)

tab1, tab2 = st.tabs(["เปิดกล้อง", "เลือกรูปจากเครื่อง"])
with tab1:
    st.caption(
        "ถ้ากล้องไม่ขึ้น กรุณากด “อนุญาต” การใช้กล้องที่เบราว์เซอร์ "
        "หรือสลับไปแท็บ “เลือกรูปจากเครื่อง” แทน"
    )
    camera_photo = st.camera_input("ถ่ายรูปหน้าฉลากยา")
with tab2:
    uploaded_file = st.file_uploader("เลือกไฟล์รูปภาพ", type=["jpg", "jpeg", "png"])
    st.caption("รองรับไฟล์ JPG และ PNG เท่านั้น ถ้าเป็นไฟล์ HEIC จากไอโฟน ให้ถ่ายผ่านแท็บ “เปิดกล้อง” แทน")

input_image = camera_photo if camera_photo else uploaded_file

if input_image is not None:
    try:
        image = Image.open(input_image)
        image = ImageOps.exif_transpose(image)  # แก้ภาพหมุนผิดทิศจากมือถือ
        image = image.convert("RGB")
    except Exception:
        st.error(
            "เปิดไฟล์รูปนี้ไม่ได้ครับ กรุณาใช้ไฟล์ JPG หรือ PNG "
            "หรือถ่ายรูปใหม่ผ่านแท็บ “เปิดกล้อง”"
        )
        st.stop()
    img_id = f"{getattr(input_image, 'name', '')}-{getattr(input_image, 'size', '')}"
    st.image(image, caption="รูปที่กำลังตรวจ", use_container_width=True)

    agree = st.checkbox(
        "ฉันเข้าใจว่ารูปนี้จะถูกส่งให้ Google Gemini (AI) ประมวลผล "
        "และจะไม่ถ่ายให้เห็นใบหน้าหรือข้อมูลส่วนตัว"
    )
    st.caption(
        "แอปนี้ไม่ได้บันทึกรูปของคุณไว้เอง การประมวลผลโดย Google เป็นไปตามเงื่อนไขของ Google"
    )

    if st.button("ให้น้องกระเทียมช่วยตรวจ", type="primary", disabled=not agree):
        st.session_state.pop("result", None)
        api_key = get_api_key()
        if not api_key:
            st.error("ยังไม่ได้ตั้งค่า GEMINI_API_KEY ดูวิธีตั้งค่าในคำแนะนำของผู้พัฒนา")
            st.stop()

        blocked = try_use_quota()
        if blocked:
            st.warning(blocked)
            st.stop()

        with st.spinner("น้องกระเทียมกำลังตรวจตามกติกา อย. 5 ข้อ... (ถ้าระบบยุ่ง อาจรอนานขึ้นอีกเล็กน้อย)"):
            try:
                client = make_client(api_key)
                fda_context = (
                    f"ผู้ใช้แจ้งเลข อย. มาว่า: {fda_number}"
                    if fda_number
                    else "ผู้ใช้ไม่ได้พิมพ์เลข อย. มา"
                )

                prompt = f"""
                คุณคือ "น้องกระเทียม" มาสคอตผู้ช่วยตรวจฉลากยาอัจฉริยะในแอปพลิเคชัน "ฉลาดอ่านฉลาก"
                ลักษณะนิสัย: ใจดี ให้คำแนะนำที่เป็นมิตร เข้าใจง่ายสำหรับ อสม. และประชาชนทั่วไป

                ข้อมูลเบื้องต้น: {fda_context}

                ข้อจำกัดสำคัญที่ต้องทำตามเสมอ:
                - ตรวจได้เฉพาะสิ่งที่มองเห็นเป็นข้อความหรือภาพบนฉลากในรูปเท่านั้น
                - ห้ามบอกหรือเดาว่าผลิตภัณฑ์มีส่วนผสมต้องห้ามหรือสารปลอมปนหรือไม่ เพราะตรวจจากภาพไม่ได้
                - ห้ามยืนยันว่าเลข อย. ถูกต้อง มีอยู่จริง หรือผลิตภัณฑ์ได้รับอนุญาตจริง ให้บอกแค่ว่าเห็นเลขบนฉลากหรือไม่
                - ถ้าข้อมูลข้อไหนมองไม่เห็นหรืออ่านไม่ชัด ให้ตอบว่า "ไม่เห็นในภาพ" ห้ามเดา

                โปรดตรวจภาพฉลากสมุนไพรนี้เทียบกับ **กติกา อย. 5 ข้อหลัก** ดังนี้:
                1. องค์ประกอบบังคับ: มีชื่อผลิตภัณฑ์, เลข อย., วันผลิต/หมดอายุ ฯลฯ ครบไหม
                2. คำเตือนเฉพาะ: มียาอันตรายที่ต้องเตือนไหม (เช่น ฟ้าทะลายโจรเตือนคนท้อง หรือ ยาภายนอกมีคำว่า ห้ามรับประทาน สีแดงไหม)
                3. สีกรอบช่องทางจำหน่าย: ขายทั่วไป (กรอบเขียว) หรือเฉพาะที่ (กรอบแดง)
                   ตรวจข้อนี้เฉพาะกับยาสมุนไพร/ยาแผนโบราณเท่านั้น ถ้าเป็นผลิตภัณฑ์ประเภทอื่น
                   หรือมองไม่ชัดว่ามีกรอบหรือไม่ ให้ถือว่า "ไม่เห็นในภาพ" และห้ามนับเป็นจุดที่ไม่ผ่าน
                4. ข้อความโอ้อวด/ภาพต้องห้าม: มีคำว่า "รักษาหายขาด, ศักดิ์สิทธิ์" หรือมี "รูปตับไตไส้พุง, รูปสัตว์อวดพลัง, รูปชี้จุดแดงแก้ปวด" ไหม (ถ้ามีคือผิดกฎหมาย)
                5. สัญลักษณ์มาตรฐานและ QR/Barcode: มีสัญลักษณ์ อย., ฮาลาล, GMP หรือบาร์โค้ดให้สแกนไหม

                เมื่อตรวจสอบเสร็จแล้ว ให้แสดงผลลัพธ์ **โดยเลือกรูปแบบใดรูปแบบหนึ่ง** ดังต่อไปนี้เท่านั้น
                (บรรทัดแรกต้องขึ้นต้นด้วย ✅ หรือ ❌ หรือ ⚠️ ตามที่กำหนด และห้ามใช้เครื่องหมาย ** หรือ # ในคำตอบ):

                รูปแบบที่ 1 (ถ้าไม่พบจุดผิดปกติจากสิ่งที่เห็นบนฉลาก):
                ✅ น้องกระเทียมตรวจเรียบร้อยครับ ไม่พบจุดผิดปกติจากสิ่งที่เห็นบนฉลาก
                ชื่อผลิตภัณฑ์: [ชื่อยา ถ้าเห็น]
                เลขทะเบียนที่เห็นบนฉลาก: [เลข อย. ถ้าเห็น ถ้าไม่เห็นให้ตอบว่า ไม่เห็นในภาพ]
                สิ่งที่พบบนฉลาก: [อธิบายสั้นๆ ว่าพบอะไรบ้าง เช่น มีกรอบสีเขียว มีคำเตือนครบ]
                คำแนะนำจากน้องกระเทียม: ผลนี้ดูจากฉลากเท่านั้น ยังไม่ได้ยืนยันว่าผลิตภัณฑ์ได้รับอนุญาตจริงหรือส่วนผสมปลอดภัย กรุณาตรวจเลข อย. กับฐานข้อมูลของ อย. ก่อนใช้ทุกครั้งครับ

                รูปแบบที่ 2 (ถ้าพบจุดที่ผิดกฎ/ไม่ผ่านเกณฑ์):
                ❌ น้องกระเทียมพบจุดที่ไม่ผ่านเกณฑ์ครับ
                ชื่อผลิตภัณฑ์: [ชื่อยา ถ้าเห็น]
                จุดที่ 1: [ระบุจุดที่ผิด เช่น พบคำโฆษณาโอ้อวด หรือพบรูปอวัยวะภายใน]
                จุดที่ 2: [ระบุจุดที่ผิดข้อต่อไป ถ้ามี]
                คำแนะนำจากน้องกระเทียม: [แนะนำสิ่งที่ควรแก้ไข หรือเตือนผู้บริโภคให้หลีกเลี่ยง]

                รูปแบบที่ 3 (ถ้าภาพเบลอ มืด อ่านข้อความไม่ออก เห็นฉลากไม่ครบ หรือไม่ใช่ภาพฉลากผลิตภัณฑ์):
                ⚠️ ภาพยังไม่ชัดพอให้ตรวจ กรุณาถ่ายใหม่ครับ
                สาเหตุ: [เช่น ภาพเบลอ แสงน้อย เห็นฉลากไม่ครบ หรือไม่ใช่ฉลากผลิตภัณฑ์]
                วิธีถ่ายใหม่: [แนะนำสั้นๆ เช่น ถ่ายในที่สว่าง ถือกล้องนิ่งๆ ให้เห็นเลข อย. ชัด]
                """

                # ส่งรูปขนาดเดิม (ไม่ย่อความละเอียด) แต่เข้ารหัสเป็น JPEG คุณภาพสูง
                # เพื่อให้ไฟล์เล็กกว่า PNG และส่งเร็วขึ้น
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=92)
                image_part = types.Part.from_bytes(
                    data=buf.getvalue(), mime_type="image/jpeg"
                )

                t0 = time.time()
                response = generate_with_retry(client, prompt, image_part)
                print(f"[timing] gemini {time.time() - t0:.1f}s", flush=True)

                st.session_state["result"] = {"text": response.text, "img": img_id}

            except Exception as e:
                kind = _err_kind(e)
                if kind in ("quota", "busy", "notfound"):
                    refund_quota()  # ระบบ AI ล้มเหลว ไม่นับเป็นสิทธิ์ที่ใช้ไป
                if kind == "quota":
                    st.warning(
                        "วันนี้มีผู้ใช้น้องกระเทียมเต็มจำนวนแล้ว "
                        "กรุณาลองใหม่อีกครั้งในภายหลังหรือพรุ่งนี้ครับ"
                    )
                elif kind in ("busy", "notfound"):
                    st.warning(
                        "น้องกระเทียมลองหลายรอบแล้วแต่ระบบ AI ของ Google ยังไม่ว่าง "
                        "กรุณากดตรวจอีกครั้งในอีกสักครู่ครับ (ไม่นับเป็นสิทธิ์ที่ใช้ไป)"
                    )
                else:
                    st.error(f"เกิดข้อผิดพลาด: {e}")

    # แสดงผลจาก session_state เพื่อให้ปุ่มฟังเสียงกดได้โดยผลไม่หาย
    saved = st.session_state.get("result")
    if saved and saved.get("img") == img_id:
        st.subheader("ผลการตรวจ")
        status, speech = show_ai_result(saved["text"])

        if speech:
            voice_section(speech)

        if status == "fail":
            st.markdown("**พบฉลากที่น่าสงสัย?** แจ้งเรื่องได้ที่สายด่วน อย.")
            st.link_button(f"โทรสายด่วน อย. {HOTLINE}", f"tel:{HOTLINE}")

        st.caption(
            "ผลนี้เป็นการคัดกรองเบื้องต้นจากข้อความและภาพบนฉลากเท่านั้น "
            "ไม่ได้ตรวจส่วนผสมจริง ไม่ได้ยืนยันการอนุญาตของ อย. และไม่ใช่คำแนะนำทางการแพทย์ "
            "หากสงสัยควรตรวจเลข อย. กับฐานข้อมูลทางการและปรึกษาเภสัชกร"
        )

# 3. เกร็ดความรู้
with st.expander("5 จุดสังเกตฉลากยาฉบับเข้าใจง่าย (แตะเพื่ออ่าน)"):
    st.markdown("""
    **ก่อนซื้อยาสมุนไพร มีจุดสังเกตเบื้องต้นง่ายๆ:**

    1. **หาเลข อย. ให้เจอ:** บนกล่องต้องมีคำว่า "เลขทะเบียนที่...", "เลขแจ้งรายละเอียดที่..." หรือ "เลขจดแจ้งที่..." ให้เห็นชัดเจน
    2. **ต้องไม่มีรูปตับไตไส้พุง:** ยาที่ปลอดภัยจะไม่เอารูปอวัยวะภายในมาโชว์เพื่ออ้างว่ารักษาตรงจุดนั้นได้
    3. **ไม่ใช้รูปสัตว์อวดพลัง:** การใช้รูป ช้าง ม้า วัว ควาย แรด ถือเป็นการโอ้อวดพละกำลัง ซึ่ง อย. ไม่อนุญาต
    4. **ไม่ชี้จุดแดงแก้ปวด:** รูปคนแล้วมีจุดสีแดงๆ ตามข้อต่อ หรือกระดูก อย. ห้ามใช้เพราะมักจะทำให้เข้าใจผิด
    5. **ข้อควรระวังคนท้อง:** ถ้ายาตัวนั้นมีส่วนผสมของ "ฟ้าทะลายโจร" บนฉลากต้องเขียนเตือนชัดเจนว่า ห้ามสตรีมีครรภ์และให้นมบุตรใช้
    """)

render_footer()

# ปุ่มลิงก์เอกสารอ้างอิง (สำหรับคนที่เปิดบนมือถือและสแกน QR จากหน้าจอเดียวกันไม่ได้)
if REFERENCE_URL:
    st.link_button("เปิดเอกสารอ้างอิง กองผลิตภัณฑ์สมุนไพร อย.", REFERENCE_URL)