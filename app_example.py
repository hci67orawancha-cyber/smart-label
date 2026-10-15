import streamlit as st
from header import render_header, result

st.set_page_config(page_title="ฉลาดอ่านฉลาก", page_icon="🧄", layout="centered")
render_header()          # วางบรรทัดนี้ไว้บนสุด แล้วโค้ดเดิมของคุณต่อจากนี้

result(True,  "พบเลขทะเบียน อย.", "G 123/2567 ตรงกับรูปแบบที่กำหนด")
result(False, "พบข้อความโอ้อวด", "ฉลากเขียนว่า 'หายขาดภายใน 7 วัน' ซึ่งไม่ได้รับอนุญาต")
