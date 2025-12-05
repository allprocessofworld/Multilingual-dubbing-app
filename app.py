import streamlit as st
import os
import re
import asyncio
import edge_tts
from pydub import AudioSegment
from pydub.effects import speedup
import tempfile
import zipfile
import io

# ==========================================
# 1. 설정 및 언어 매핑
# ==========================================
st.set_page_config(page_title="AI 자동 더빙 생성기", page_icon="🎙️")

VOICE_MAPPING = {
    "ko": "ko-KR-SunHiNeural",
    "en": "en-US-ChristopherNeural",
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KillianNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    # 필요한 언어 추가
}
DEFAULT_VOICE = "en-US-ChristopherNeural"

# ==========================================
# 2. 핵심 로직 함수들
# ==========================================
def parse_sbv_time(time_str):
    h, m, s = time_str.split(':')
    s, ms = s.split('.')
    return (int(h) * 3600000) + (int(m) * 60000) + (int(s) * 1000) + int(ms)

def parse_sbv_content(content):
    pattern = re.compile(r'(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+)\n(.+?)(?=\n\n|$)', re.DOTALL)
    matches = pattern.findall(content)
    parsed_data = []
    for start, end, text in matches:
        start_ms = parse_sbv_time(start)
        end_ms = parse_sbv_time(end)
        parsed_data.append({
            'start': start_ms,
            'end': end_ms,
            'duration': end_ms - start_ms,
            'text': text.replace('\n', ' ').strip()
        })
    return parsed_data

def fit_audio_to_duration(audio_seg, max_duration_ms):
    current_duration = len(audio_seg)
    if current_duration <= max_duration_ms:
        return audio_seg
    speed_factor = current_duration / max_duration_ms
    # 속도 조절 (pydub speedup 활용)
    new_sample_rate = int(audio_seg.frame_rate * speed_factor)
    fast_audio = audio_seg._spawn(audio_seg.raw_data, overrides={'frame_rate': new_sample_rate})
    return fast_audio.set_frame_rate(audio_seg.frame_rate)

async def generate_audio_for_file(sbv_content, filename):
    lang_code = filename.split('.')[0]
    voice = VOICE_MAPPING.get(lang_code, DEFAULT_VOICE)
    
    subtitles = parse_sbv_content(sbv_content)
    final_audio = AudioSegment.empty()
    current_cursor = 0

    # 진행률 표시줄
    progress_bar = st.progress(0)
    total_lines = len(subtitles)

    for i, sub in enumerate(subtitles):
        text = sub['text']
        if not text: continue

        # TTS 생성 (메모리 내에서 처리)
        communicate = edge_tts.Communicate(text, voice)
        mp3_fp = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_fp.write(chunk["data"])
        
        mp3_fp.seek(0)
        segment = AudioSegment.from_mp3(mp3_fp)
        
        # 길이 맞추기
        processed_segment = fit_audio_to_duration(segment, sub['duration'])
        
        # 싱크 맞추기 (무음 추가)
        silence_gap = sub['start'] - current_cursor
        if silence_gap > 0:
            final_audio += AudioSegment.silent(duration=silence_gap)
        
        final_audio += processed_segment
        current_cursor = sub['start'] + len(processed_segment)
        
        # 진행률 업데이트
        progress_bar.progress((i + 1) / total_lines)

    progress_bar.empty() # 완료 후 바 숨김
    
    # 결과 WAV를 메모리에 저장
    out_wav = io.BytesIO()
    final_audio.export(out_wav, format="wav")
    out_wav.seek(0)
    return out_wav

# ==========================================
# 3. 웹 앱 UI 구성
# ==========================================
st.title("🎙️ 다국어 자동 더빙 생성기")
st.write("YouTube .sbv 자막 파일을 업로드하면, 타임코드에 딱 맞는 더빙 오디오(.wav)를 만들어줍니다.")

uploaded_files = st.file_uploader("SBV 파일들을 드래그해서 넣으세요 (여러 개 가능)", 
                                  type=["sbv"], accept_multiple_files=True)

if uploaded_files:
    if st.button("오디오 생성 시작!"):
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            for uploaded_file in uploaded_files:
                filename = uploaded_file.name
                st.write(f"🔄 처리 중: **{filename}**...")
                
                # 파일 내용 읽기
                stringio = io.StringIO(uploaded_file.getvalue().decode("utf-8"))
                sbv_content = stringio.read()
                
                # 비동기 로직 실행
                wav_data = asyncio.run(generate_audio_for_file(sbv_content, filename))
                
                # 압축 파일에 추가
                output_filename = filename.replace('.sbv', '.wav')
                zf.writestr(output_filename, wav_data.getvalue())
                st.success(f"✅ 완료: {filename}")
        
        zip_buffer.seek(0)