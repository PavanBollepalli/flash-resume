"""Add timed caption overlays to a demo video for Flash Resume.

Captions are rendered with Pillow (for reliable emoji/unicode glyph support)
and composited onto the video with FFmpeg using the overlay filter.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Caption = dict  # { "text": str, "start": float, "end": float, "emoji": bool }

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOX_BG_RGBA = (0, 0, 0, 191)  # black @ 0.75 alpha (191/255)
TEXT_COLOR = (255, 255, 255, 255)
BOX_BORDER = 10
FONT_SIZE = 48
DEFAULT_FONT_PATH = r"C:\Windows\Fonts\segoeuib.ttf"
EMOJI_FONT_PATH = r"C:\Windows\Fonts\seguisym.ttf"  # Segoe UI Symbol has ⚡
DEFAULT_EMOJI = "\u26a1"  # ⚡

CAPTIONS: list[Caption] = [
    {"text": "Still tailoring resumes manually?", "start": 0.0, "end": 4.5},
    {"text": "JD \u2192 AI \u2192 Overleaf", "start": 4.5, "end": 10.0},
    {"text": "\u26a1 Tailored to the job. In <7 seconds.", "start": 11.0, "end": 13.5, "emoji": True},
    {"text": "ATS-ready. 1 page. Done.", "start": 13.5, "end": 16.0},
    {"text": "One command. That's it.", "start": 17.0, "end": 20.0},
    {"text": "pip install flash-resume", "start": 20.0, "end": 23.0},
    {"text": "One-time setup. Then you're ready.", "start": 24.0, "end": 27.0},
    {"text": "Set up once. Tailor every application.", "start": 27.0, "end": None},  # None → use full duration
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def inspect_video(path: Path) -> dict:
    """Probe a video file with ffprobe and return key metadata."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info: dict = json.loads(result.stdout)

    video_stream = next(
        (s for s in info["streams"] if s["codec_type"] == "video"), None
    )
    audio_stream = next(
        (s for s in info["streams"] if s["codec_type"] == "audio"), None
    )

    return {
        "duration": float(info["format"]["duration"]),
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "fps": eval(video_stream.get("avg_frame_rate", "30/1")),  # type: ignore[arg-type]
        "has_audio": audio_stream is not None,
        "video_codec": video_stream["codec_name"],
        "video_stream_index": video_stream["index"],
        "audio_stream_index": audio_stream["index"] if audio_stream else None,
    }


def resolve_font(font_file: str | None) -> str:
    """Return a usable font path, verifying it can render ."""
    candidates: list[str] = []

    if font_file:
        candidates.append(font_file)
    else:
        candidates.append(DEFAULT_FONT_PATH)
        # Additional bold sans-serif fallbacks
        candidates.append(r"C:\Windows\Fonts\arialbd.ttf")
        candidates.append(r"C:\Windows\Fonts\bahnschrift.ttf")

    for path in candidates:
        p = Path(path)
        if not p.is_file():
            logger.debug("Font not found: %s", path)
            continue
        # Verify  renders (not a tofu box)
        try:
            font = ImageFont.truetype(str(p), FONT_SIZE)
            bbox = font.getbbox(DEFAULT_EMOJI)
            if bbox is not None and (bbox[2] - bbox[0] > 0 or bbox[3] - bbox[1] > 0):
                logger.info("Selected font: %s", path)
                return str(p)
        except Exception as exc:
            logger.debug("Cannot load font %s: %s", path, exc)

    msg = (
        "No suitable font found that can render the ⚡ character.\n"
        "Provide one explicitly with --font-file.\n"
        "Example: python add_captions.py input.mp4 output.mp4 "
        "--font-file 'C:\\path\\to\\font.ttf'"
    )
    raise FileNotFoundError(msg)


def has_emoji(text: str) -> bool:
    """Check if text contains characters that may need emoji font fallback."""
    for ch in text:
        if ord(ch) > 0x2600:  # rough heuristic for symbols/emoji
            return True
    return False


def render_caption_image(
    text: str,
    font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont | None,
    box_bg: tuple[int, int, int, int],
    text_color: tuple[int, int, int, int],
    border: int,
) -> Image.Image:
    """Render a single caption line as an RGBA image with a semi-transparent box."""
    # Split off leading emoji if present
    leading_emoji = ""
    rest = text
    if text.startswith(DEFAULT_EMOJI):
        leading_emoji = DEFAULT_EMOJI
        rest = text[len(DEFAULT_EMOJI):].lstrip(" ")

    # Use emoji font for the emoji part, text font for the rest
    active_font = font
    if leading_emoji and emoji_font:
        # Measure combined width
        e_bbox = emoji_font.getbbox(leading_emoji) or (0, 0, 0, 0)
        e_width = e_bbox[2] - e_bbox[0]
        rest_bbox = font.getbbox(rest) or (0, 0, 0, 0)
        rest_width = rest_bbox[2] - rest_bbox[0]
        # Space between emoji and text
        space_width = font.getlength(" ")
        total_text_width = int(e_width + space_width + rest_width)
    else:
        total_text_width = int(font.getlength(text))

    # Text height
    text_height = FONT_SIZE + 4  # small margin

    img_width = total_text_width + 2 * border + 4  # extra padding
    img_height = text_height + 2 * border + 4

    img = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw background box
    draw.rectangle(
        [(0, 0), (img_width - 1, img_height - 1)],
        fill=box_bg,
    )

    # Draw text
    y_offset = border + 2
    x_offset = border + 2

    if leading_emoji and emoji_font:
        draw.text((x_offset, y_offset), leading_emoji, fill=text_color, font=emoji_font)
        e_bbox = emoji_font.getbbox(leading_emoji) or (0, 0, 0, 0)
        e_width = e_bbox[2] - e_bbox[0]
        x_offset += int(e_width + space_width)
        draw.text((x_offset, y_offset), rest, fill=text_color, font=font)
    else:
        draw.text((x_offset, y_offset), text, fill=text_color, font=font)

    return img


def render_all_captions(
    captions: list[Caption],
    width: int,
    height: int,
    font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont | None,
    tmpdir: Path,
) -> list[dict]:
    """Render each caption to a PNG and return overlay metadata.

    Returns a list of dicts with keys:
      - png_path: absolute path to the rendered caption image
      - start: when the caption appears (seconds)
      - end: when it disappears (seconds) or None for end-of-video
      - x, y: pixel position on the video frame
    """
    bottom_margin = height * 0.10

    results: list[dict] = []
    for idx, cap in enumerate(captions):
        img = render_caption_image(
            cap["text"], font, emoji_font,
            BOX_BG_RGBA, TEXT_COLOR, BOX_BORDER,
        )
        png_path = tmpdir / f"caption_{idx:02d}.png"
        img.save(str(png_path))
        logger.debug("Rendered caption %d → %s", idx, png_path)

        # Position: centered horizontally, bottom_margin above bottom edge
        caption_y = int(height - bottom_margin - img.height)
        caption_x = (width - img.width) // 2

        results.append({
            "png_path": str(png_path),
            "start": cap["start"],
            "end": cap.get("end"),
            "x": caption_x,
            "y": caption_y,
        })

    return results


def build_filter_complex(
    overlays: list[dict],
    duration: float,
) -> str:
    """Build an FFmpeg -filter_complex string for all caption overlays."""
    if not overlays:
        return "[0:v]copy[v]"

    # Build chain: start with [0:v], overlay each caption PNG sequentially
    parts: list[str] = []
    for idx, ov in enumerate(overlays):
        end_val = ov["end"] if ov["end"] is not None else duration
        enable_expr = f"between(t,{ov['start']},{end_val})"

        # Input labels: video input is [0:v] for first, then [vN] for subsequent
        # PNG input is [{idx+1}:v] (since video is input 0)
        src = "[0:v]" if idx == 0 else f"[v{idx-1}]"
        dst = f"[v{idx}]" if idx < len(overlays) - 1 else "[v]"

        parts.append(
            f"{src}[{idx+1}:v]overlay={ov['x']}:{ov['y']}"
            f":enable='{enable_expr}'{dst}"
        )

    return ";".join(parts)


def run_ffmpeg(
    input_path: Path,
    output_path: Path,
    overlays: list[dict],
    filter_complex: str,
    info: dict,
) -> None:
    """Execute FFmpeg to composite captions onto the video."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
    ]
    # Add each caption PNG as an input
    for ov in overlays:
        cmd.extend(["-i", str(ov["png_path"])])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "veryfast",
        "-c:a", "copy",
        str(output_path),
    ])
    logger.info("Running FFmpeg: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"FFmpeg error (exit code {result.returncode}):", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    logger.info("FFmpeg completed successfully")


def verify_output(output_path: Path, info: dict) -> None:
    """Probe the output file and compare key properties to the input."""
    if not output_path.exists():
        logger.error("Output file does not exist: %s", output_path)
        sys.exit(1)

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out_info = json.loads(result.stdout)

    video_stream = next(
        (s for s in out_info["streams"] if s["codec_type"] == "video"), None
    )
    audio_stream = next(
        (s for s in out_info["streams"] if s["codec_type"] == "audio"), None
    )
    out_duration = float(out_info["format"]["duration"])

    logger.info("Output verification:")
    logger.info("  Duration: %.2fs (input: %.2fs)", out_duration, info["duration"])
    if video_stream:
        logger.info(
            "  Resolution: %dx%d (input: %dx%d)",
            video_stream["width"], video_stream["height"],
            info["width"], info["height"],
        )
        logger.info("  Video codec: %s (input: %s)", video_stream["codec_name"], info["video_codec"])
    else:
        logger.error("  No video stream found in output!")
        sys.exit(1)

    if info["has_audio"]:
        if audio_stream:
            logger.info("  Audio stream: present ✓")
        else:
            logger.warning("  Audio stream missing in output (input had audio)")
    else:
        logger.info("  Audio stream: none (input had none)")

    # Size comparison
    out_size = output_path.stat().st_size
    logger.info("  File size: %d bytes", out_size)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add timed caption overlays to a demo video for Flash Resume."
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Path to the input MP4 video file",
    )
    parser.add_argument(
        "output_path",
        type=Path,
        help="Path for the output MP4 video file",
    )
    parser.add_argument(
        "--font-file",
        type=str,
        default=None,
        help="Path to a TrueType font file (must support bold and ideally emoji)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Validate input
    input_path = args.input_path.resolve()
    if not input_path.exists():
        logger.error("Input file does not exist: %s", input_path)
        sys.exit(1)
    if not input_path.is_file():
        logger.error("Input path is not a file: %s", input_path)
        sys.exit(1)

    output_path = args.output_path.resolve()
    # Never overwrite the source
    if output_path.resolve() == input_path.resolve():
        logger.error("Output path must not be the same as input path")
        sys.exit(1)

    # Create output directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Check FFmpeg/ffprobe availability
    if not shutil.which("ffmpeg"):
        logger.error("ffmpeg not found in PATH. Install FFmpeg and try again.")
        sys.exit(1)
    if not shutil.which("ffprobe"):
        logger.error("ffprobe not found in PATH. Install FFmpeg and try again.")
        sys.exit(1)

    # Inspect input
    logger.info("Inspecting input: %s", input_path)
    info = inspect_video(input_path)
    logger.info(
        "  Duration: %.2fs | %dx%d @ %.2f fps | audio: %s | codec: %s",
        info["duration"], info["width"], info["height"],
        info["fps"], info["has_audio"], info["video_codec"],
    )

    # Resolve font
    font_path = resolve_font(args.font_file)
    font = ImageFont.truetype(font_path, FONT_SIZE)

    # Emoji font (Segoe UI Symbol)
    emoji_font: ImageFont.FreeTypeFont | None = None
    if Path(EMOJI_FONT_PATH).is_file():
        try:
            emoji_font = ImageFont.truetype(EMOJI_FONT_PATH, FONT_SIZE)
        except Exception:
            pass

    # Update caption end times to use actual duration
    duration = info["duration"]
    for cap in CAPTIONS:
        if cap.get("end") is None:
            cap["end"] = duration
        # Clamp end to duration
        if cap["end"] is not None and cap["end"] > duration:
            cap["end"] = duration

    # Render captions with Pillow
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        logger.info("Rendering %d captions with Pillow...", len(CAPTIONS))
        overlays = render_all_captions(
            CAPTIONS, info["width"], info["height"],
            font, emoji_font, tmp,
        )

        # Build FFmpeg filter
        filter_complex = build_filter_complex(overlays, duration)
        logger.debug("Filter complex:\n%s", filter_complex)

        # Run FFmpeg
        logger.info("Compositing captions onto video...")
        run_ffmpeg(input_path, output_path, overlays, filter_complex, info)

    # Verify output
    verify_output(output_path, info)
    logger.info("Done → %s", output_path)
    print(f"Output written to {output_path}")


if __name__ == "__main__":
    main()