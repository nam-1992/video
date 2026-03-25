import glob
import os
import shutil
import subprocess

# ================= CONFIG =================

FPS = 30

FOCUS_MINUTES = 30
BREAK_MINUTES = 5
SESSIONS = 2

TEXT_DURATION = 5
TEXT_FADE = 1

TIMER_FONT_SIZE = 180
FONT_PATH = "arial.ttf"

BELL_DURATION = 5
INTRO_BELL_DURATION = 1

FADE_VIDEO = 0.5
FADE_AUDIO = 0.5

FOCUS_VOLUME = 1.0
BREAK_VOLUME = 0.6

# Separate looped background videos
BG_TEXT_VIDEO = "bg_text.mp4"
BG_FOCUS_VIDEO = "bg_focus.mp4"
BG_BREAK_VIDEO = "bg_break.mp4"

FINAL_OUTPUT = "pomodoro_final.mp4"
TEMP_DIR = "render_temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# Keep ultrafast by default for fastest render while preserving structure
VIDEO_PRESET = "ultrafast"
AUTO_USE_NVENC = True
# ==========================================


def run(cmd):
    subprocess.run(cmd, check=True)


def has_encoder(encoder_name):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and encoder_name in result.stdout


def video_encode_args():
    """
    Pick the fastest available encoder.
    - Prefer NVIDIA NVENC when available.
    - Fallback to CPU libx264 ultrafast.
    """
    if AUTO_USE_NVENC and has_encoder("h264_nvenc"):
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "30", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", VIDEO_PRESET, "-pix_fmt", "yuv420p"]


# ================= MUSIC BUILDER =================

def build_music_input(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.*")))
    files = [f for f in files if f.lower().endswith((".mp3", ".m4a", ".wav"))]

    if not files:
        return None

    safe_folder_name = os.path.basename(os.path.normpath(folder))
    list_file = os.path.join(TEMP_DIR, f"{safe_folder_name}_list.txt")

    with open(list_file, "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{os.path.abspath(file)}'\n")

    concat_output = os.path.join(TEMP_DIR, f"{safe_folder_name}_concat.m4a")

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        # Re-encode to keep concat stable even when input .m4a files differ.
        "-vn",
        "-c:a", "aac",
        "-b:a", "192k",
        concat_output,
    ])

    return concat_output


# ================= TIMER BLOCK =================

def render_timer_block(bg_video, minutes, music_folder, volume, output, is_focus=False):
    seconds = minutes * 60
    total_duration = seconds + BELL_DURATION

    timer_text = (
        f"%{{eif\\:max({seconds}-floor(t)\\,0)/60\\:d\\:2}}\\:"
        f"%{{eif\\:mod(max({seconds}-floor(t)\\,0)\\,60)\\:d\\:2}}"
    )

    music_file = build_music_input(music_folder)

    # 0:v = looped background video
    inputs = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", bg_video]
    input_index = 1
    filter_parts = []

    # MUSIC
    if music_file:
        inputs += ["-stream_loop", "-1", "-i", music_file]
        filter_parts.append(
            f"[{input_index}:a]atrim=0:{seconds},"
            f"afade=t=in:st=0:d={FADE_AUDIO},"
            f"afade=t=out:st={seconds-FADE_AUDIO}:d={FADE_AUDIO},"
            f"volume={volume}[music]"
        )
        input_index += 1

    # INTRO BELL (focus only)
    if is_focus and os.path.exists("intro_bell_focus.m4a"):
        inputs += ["-i", "intro_bell_focus.m4a"]
        filter_parts.append(f"[{input_index}:a]atrim=0:{INTRO_BELL_DURATION}[intro]")
        input_index += 1

    # END BELL
    inputs += ["-i", "bell.m4a"]
    filter_parts.append(
        f"[{input_index}:a]atrim=0:{BELL_DURATION},"
        f"adelay={seconds * 1000}|{seconds * 1000}[endbell]"
    )

    # AUDIO MIX
    mix_inputs = []
    if music_file:
        mix_inputs.append("[music]")
    if is_focus and os.path.exists("intro_bell_focus.m4a"):
        mix_inputs.append("[intro]")
    mix_inputs.append("[endbell]")
    filter_parts.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}[a]")

    # VIDEO + TIMER
    filter_parts.append(
        f"[0:v]trim=duration={total_duration},setpts=PTS-STARTPTS,"
        f"drawtext=fontfile={FONT_PATH}:"
        f"text='{timer_text}':"
        f"fontcolor=white:borderw=6:bordercolor=black:"
        f"fontsize={TIMER_FONT_SIZE}:"
        f"x=(w/6-text_w/2):y=(h/6-text_h/2),"
        f"fade=t=in:st=0:d={FADE_VIDEO},"
        f"fade=t=out:st={total_duration-FADE_VIDEO}:d={FADE_VIDEO}[v]"
    )

    cmd = inputs + [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]",
        "-map", "[a]",
        "-t", str(total_duration),
        "-r", str(FPS),
    ] + video_encode_args() + [
        os.path.join(TEMP_DIR, output),
    ]

    run(cmd)


# ================= TEXT BLOCK =================

def render_text_block(text_image, index):
    output = f"text_{index}.mp4"
    total = TEXT_DURATION

    filter_complex = (
        f"[0:v]trim=duration={total},setpts=PTS-STARTPTS[bg];"
        f"[bg][1:v]overlay=(W-w)/2:(H-h)/2,"
        f"fade=t=in:st=0:d={TEXT_FADE},"
        f"fade=t=out:st={total-TEXT_FADE}:d={TEXT_FADE}[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", BG_TEXT_VIDEO,
        "-loop", "1", "-i", text_image,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "2:a",
        "-t", str(total),
        "-r", str(FPS),
    ] + video_encode_args() + [
        os.path.join(TEMP_DIR, output),
    ]

    run(cmd)
    return output


# ================= BUILD =================

def validate_inputs():
    required = [BG_TEXT_VIDEO, BG_FOCUS_VIDEO, BG_BREAK_VIDEO, "bell.m4a"]
    missing = [f for f in required if not os.path.exists(f)]
    if missing:
        raise FileNotFoundError(f"Missing required file(s): {', '.join(missing)}")


validate_inputs()

print("Rendering focus...")
render_timer_block(BG_FOCUS_VIDEO, FOCUS_MINUTES, "focus_music", FOCUS_VOLUME, "focus.mp4", True)

print("Rendering break...")
render_timer_block(BG_BREAK_VIDEO, BREAK_MINUTES, "break_music", BREAK_VOLUME, "break.mp4", False)

blocks = []

# Load text files sorted
text_files = []
if os.path.exists("text"):
    for f in os.listdir("text"):
        if f.endswith(".png"):
            try:
                num = int(os.path.splitext(f)[0])
                text_files.append((num, f))
            except ValueError:
                pass

text_files.sort()
text_index = 0

# 3 text đầu
for _ in range(3):
    if text_index < len(text_files):
        print("Rendering text:", text_files[text_index][1])
        name = render_text_block(os.path.join("text", text_files[text_index][1]), text_index)
        blocks.append(name)
        text_index += 1

# Sessions loop
for _ in range(SESSIONS):
    blocks.append("focus.mp4")

    if text_index < len(text_files):
        print("Rendering text:", text_files[text_index][1])
        name = render_text_block(os.path.join("text", text_files[text_index][1]), text_index)
        blocks.append(name)
        text_index += 1

    blocks.append("break.mp4")

    if text_index < len(text_files):
        print("Rendering text:", text_files[text_index][1])
        name = render_text_block(os.path.join("text", text_files[text_index][1]), text_index)
        blocks.append(name)
        text_index += 1

# CONCAT FINAL

with open(os.path.join(TEMP_DIR, "list.txt"), "w", encoding="utf-8") as f:
    for b in blocks:
        f.write(f"file '{os.path.abspath(os.path.join(TEMP_DIR, b))}'\n")

print("Concatenating final video...")
run([
    "ffmpeg", "-y",
    "-f", "concat",
    "-safe", "0",
    "-i", os.path.join(TEMP_DIR, "list.txt"),
    "-c", "copy",
    FINAL_OUTPUT,
])

shutil.rmtree(TEMP_DIR)

print("✅ DONE:", FINAL_OUTPUT)
