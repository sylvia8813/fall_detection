"""
Tera Term log -> keypoint visualization
Parses INVOKE JSON lines from teraterm.log, decodes the base64 jpeg,
and overlays bbox + 17 COCO keypoints (with confidence labels) per frame.

Usage:
    python3 visualize_keypoints.py C:\path\to\10_02_teraterm.log --out output2
    python3 visualize_keypoints.py C:\PoseEstimation\log_visualizer\log_20260601_111422.log --out output_0601_1116                 
"""

import argparse
import base64
import io
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

# COCO-17 joint names (same order YOLOv8-pose uses)
COCO_NAMES = [
    "nose", "L_eye", "R_eye", "L_ear", "R_ear",
    "L_sho", "R_sho", "L_elb", "R_elb", "L_wri", "R_wri",
    "L_hip", "R_hip", "L_kne", "R_kne", "L_ank", "R_ank",
]

# COCO-17 skeleton edges (pairs of indices)
SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),               # face
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),      # arms
    (5, 11), (6, 12), (11, 12),                    # torso
    (11, 13), (13, 15), (12, 14), (14, 16),       # legs
]

# Color per confidence bucket (RGB)
def conf_color(c):
    if c >= 50:
        return (0, 220, 0)        # green - strong
    if c >= 20:
        return (255, 200, 0)      # amber - weak
    return (255, 60, 60)          # red - very weak / likely noise


def parse_invoke_lines(log_path):
    """Yield (frame_idx, json_obj) for every INVOKE line in the log."""
    frame_idx = 0
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if '"name": "INVOKE"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # try to recover trailing junk
                m = re.search(r"(\{.*\})", line)
                if not m:
                    continue
                obj = json.loads(m.group(1))
            yield frame_idx, obj
            frame_idx += 1


def draw_frame(obj, min_conf=0):
    """Return a PIL.Image with bbox + keypoints overlaid."""
    data = obj.get("data", {})
    img_b64 = data.get("image", "")
    if not img_b64:
        return None
    img = Image.open(io.BytesIO(base64.b64decode(img_b64))).convert("RGB")
    # The device usually streams a small (320x240) frame; upscale 2x for legibility
    scale = 2
    img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 11)
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
        font_big = font

    kps_list = data.get("keypoints", [])
    if not kps_list:
        draw.text((6, 6), "NO DETECTION (keypoints: [])",
                  fill=(255, 0, 0), font=font_big)
        return img

    for det in kps_list:
        # det = [ [bx, by, bw, bh, score, cls], [ [x,y,conf,idx], ... ] ]
        if not det or len(det) < 2:
            continue
        bbox, kpts = det[0], det[1]
        bx, by, bw, bh, score, cls = bbox
        bx, by, bw, bh = bx * scale, by * scale, bw * scale, bh * scale
        draw.rectangle([bx, by, bx + bw, by + bh], outline=(0, 180, 255), width=2)
        draw.text((bx + 2, by + 2), f"bbox score={score}",
                  fill=(0, 180, 255), font=font)

        # collect points (idx -> (x,y,conf))
        pts = {}
        for kp in kpts:
            if len(kp) < 4:
                continue
            x, y, c, idx = kp[0] * scale, kp[1] * scale, kp[2], kp[3]
            pts[idx] = (x, y, c)

        # draw skeleton lines first (only when both endpoints meet min_conf)
        for a, b in SKELETON:
            if a in pts and b in pts:
                xa, ya, ca = pts[a]
                xb, yb, cb = pts[b]
                if ca >= min_conf and cb >= min_conf:
                    draw.line([xa, ya, xb, yb], fill=(180, 180, 180), width=1)

        # draw keypoints
        for idx, (x, y, c) in pts.items():
            if c < min_conf:
                continue
            r = 4
            color = conf_color(c)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(0, 0, 0))
            label = f"{COCO_NAMES[idx] if idx < 17 else idx}:{c}"
            draw.text((x + 5, y - 5), label, fill=color, font=font)

    # legend
    legend = "green>=50  amber>=20  red<20"
    draw.text((6, img.height - 16), legend, fill=(255, 255, 255), font=font)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="path to teraterm.log")
    ap.add_argument("--out", default="output", help="output directory")
    ap.add_argument("--min-conf", type=int, default=0,
                    help="hide keypoints below this confidence (default 0 = show all)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    summary = []
    for idx, obj in parse_invoke_lines(args.log):
        img = draw_frame(obj, min_conf=args.min_conf)
        if img is None:
            continue
        out_path = os.path.join(args.out, f"frame_{idx:03d}.png")
        img.save(out_path)
        kps = obj.get("data", {}).get("keypoints", [])
        n = 0
        high = 0
        if kps:
            for det in kps:
                if len(det) >= 2:
                    for kp in det[1]:
                        n += 1
                        if len(kp) >= 3 and kp[2] >= 50:
                            high += 1
        summary.append((idx, len(kps), n, high))
        print(f"frame {idx:03d}: detections={len(kps)} kpts={n} high_conf>=50={high} -> {out_path}")

    print("\n=== summary ===")
    print(f"{'frame':>5} {'detections':>10} {'kpts':>5} {'>=50':>5}")
    for i, d, n, h in summary:
        print(f"{i:5d} {d:10d} {n:5d} {h:5d}")


if __name__ == "__main__":
    main()
