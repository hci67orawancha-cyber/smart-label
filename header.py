import base64
import os

import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;500&family=Sarabun:wght@400;600;700&display=swap');

html, body, [class*="st-"], .stMarkdown, button, input, textarea {
  font-family: 'Sarabun', sans-serif;
}
.stApp { font-size: 19px; line-height: 1.65; }
[data-testid="stIconMaterial"], .material-icons, .material-symbols-rounded {
  font-family: "Material Symbols Rounded", "Material Icons" !important;
}
[data-testid="stWidgetLabel"] p { font-size: 1.05rem; font-weight: 600; }
[data-testid="stHeaderActionElements"] { display: none; }

/* หัวข้อ: ใช้ Prompt เหมือนชื่อเว็บ */
.stApp h2, .stApp h3 { font-family: 'Prompt', sans-serif; font-weight: 500; }

/* แถบพับคู่มือ: พื้นเขียวอ่อน ไม่มีเส้นขอบ */
[data-testid="stExpander"] details { background: #e4eadf; border: 0; border-radius: 10px; }
[data-testid="stExpander"] summary { font-weight: 700; padding: .9rem 1rem; }
.block-container { padding: 0 1rem 6rem !important; max-width: 980px; }
header[data-testid="stHeader"] { background: transparent; }

/* ---------- หัวเว็บ: ใช้ภาพแบนเนอร์ที่ขยับได้ทั้งแผ่น ---------- */
.hero { background: #293e31; margin: 0 -1rem 1.8rem; line-height: 0; }
.hero img { display: block; width: 100%; height: auto; }

/* ---------- ผลตรวจ: ใช้แถบสีด้านข้างบอกสถานะ ไม่ใช้การ์ดเหมือนกันทุกอัน ---------- */
.res { padding: .7rem 1rem; margin: .5rem 0; border-left: 8px solid; background: #fff; }
.res.ok   { border-color: #2f7d4f; }
.res.fail { border-color: #b3382c; background: #fbefed; }
.res b { display: block; }

/* ปุ่มหลัก: เหลืองเดียวกับผีเสื้อในแบนเนอร์ */
.stButton > button[kind="primary"] {
  background: #f2c230; color: #1d2a21; border: 0; border-radius: 10px;
  font-size: 1.15rem; font-weight: 700; padding: .8rem 1.4rem; min-height: 3.2rem;
}
.stButton > button[kind="primary"]:hover { background: #e6b51f; color: #1d2a21; }
:focus-visible { outline: 3px solid #1d2a21 !important; outline-offset: 2px; }
</style>
"""

def _data_uri(name):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return "data:image/webp;base64," + base64.b64encode(f.read()).decode()


@st.cache_resource
def _hero_html():
    anim = _data_uri("banner.webp")
    still = _data_uri("banner_still.webp") or anim
    if not anim:
        return None
    alt = "ฉลาดอ่านฉลาก รู้ทันยาโอ้อวด เลือกซื้อยาสมุนไพรอย่างปลอดภัยตามหลักเกณฑ์ อย."
    return (
        '<div class="hero"><picture>'
        f'<source media="(prefers-reduced-motion: reduce)" srcset="{still}">'
        f'<img src="{anim}" alt="{alt}"></picture></div>'
    )


FALLBACK = """
<div class="hero" style="line-height:1.4;padding:2rem 1rem;text-align:center;color:#eef2ec">
  <div style="font-family:'Prompt',sans-serif;font-weight:300;font-size:2.4rem">ฉลาดอ่านฉลาก</div>
  <div style="color:#c9d6cb">รู้ทันยาโอ้อวด เลือกซื้อยาสมุนไพรอย่างปลอดภัยตามหลักเกณฑ์ อย.</div>
</div>
"""


def render_header():
    hero = _hero_html()
    if hero is None:
        st.markdown(CSS + FALLBACK, unsafe_allow_html=True)
        st.warning("ไม่พบไฟล์ static/banner.webp ให้วางไฟล์ banner.webp และ banner_still.webp ในโฟลเดอร์ static")
    else:
        st.markdown(CSS + hero, unsafe_allow_html=True)


def result(ok: bool, title: str, detail: str):
    cls = "ok" if ok else "fail"
    mark = "ผ่าน" if ok else "ไม่ผ่าน"
    st.markdown(f'<div class="res {cls}"><b>{mark}: {title}</b>{detail}</div>',
                unsafe_allow_html=True)