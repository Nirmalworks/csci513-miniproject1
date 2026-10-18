#!/usr/bin/env python
"""
make_showcase.py  --  turn your MP1 runs into HUD videos and a GitHub-ready folder.

For every scenario it makes a video with:
  * the car's dashcam on the left (colours corrected)
  * live cards: mode, my speed, lead speed, gap
  * a top-down lane view showing both cars and the gap
  * speed and gap graphs with a moving time cursor
  * PASS/FAIL for each of the 5 grading rules (from the official evaluator)

Output folder (default ~/projects/nirmal-csci-project/mp1-acc-showcase):
  videos/<scenario>.mp4      full-quality HUD video per scenario
  videos/all_scenarios.mp4   all 15 back to back
  gifs/<scenario>.gif        small previews that play directly on GitHub
  logs/<scenario>.csv        the raw traces
  controller.py              your controller
  README.md                  score table + all previews

Usage (nirmal-csci-mp1 environment active):
  python ~/projects/nirmal-csci-project/make_showcase.py --run-name attempt01
"""

import argparse
import csv
import glob
import math
import os
import re
import shutil
import subprocess
import sys

import cv2
import numpy as np

DEFAULT_PROJECT = "/home/omey/projects/nirmal-csci-project/csci513-miniproject1"
DEFAULT_OUT = "/home/omey/projects/nirmal-csci-project/mp1-acc-showcase"

RULES = [
    ("safeFollowing", "Safe gap"),
    ("forwardProgress", "No reversing"),
    ("dontStopUnlessLeadStops", "No needless stop"),
    ("notFollowWhenFarAway", "Cruise when far"),
    ("reachTargetWhenNotFollowing", "Reach 20 m/s"),
]

# Canvas and colours (OpenCV uses Blue-Green-Red order)
W, H = 1280, 720
FPS = 10
BG = (28, 24, 22)
PANEL = (46, 40, 37)
GRID = (70, 62, 58)
TEXT = (240, 238, 235)
DIM = (165, 158, 150)
GREEN = (120, 200, 90)
BLUE = (235, 160, 70)
RED = (85, 85, 235)
AMBER = (50, 185, 245)
EGO_COLOUR = (100, 110, 255)     # my car: red, like CARLA's bird's-eye view
LEAD_COLOUR = (120, 220, 120)    # lead car: green, like CARLA's bird's-eye view
FONT = cv2.FONT_HERSHEY_DUPLEX
FONT_LIGHT = cv2.FONT_HERSHEY_SIMPLEX


# ----------------------------------------------------------------------
# Reading data
# ----------------------------------------------------------------------
def read_log(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": float(r["time_elapsed"]),
                "v": float(r["ego_velocity"]),
                "d": float(r["distance_to_lead"]),
                "lead": float(r["lead_speed"]),
                "mode": int(float(r["mode"])),
            })
    return rows


def read_frames(path, fix_colours):
    if not os.path.isfile(path):
        return []
    frames = []
    cap = cv2.VideoCapture(path)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        # The simulator stores RGB frames but saves them as if they were BGR,
        # so red and blue come out swapped. Swap them back.
        frames.append(frame[:, :, ::-1].copy() if fix_colours else frame)
    cap.release()
    return frames


def rule_results(project, csv_path):
    """Run the official evaluator on one log; return {rule: True/False}.

    The evaluator only accepts files inside the assignment folder, so the log is
    copied there for a moment and passed as a relative path.
    """
    tmp_dir = os.path.join(project, "logs", "_showcase_eval")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_copy = os.path.join(tmp_dir, os.path.basename(csv_path))
    shutil.copy(csv_path, tmp_copy)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mp1_evaluation", os.path.relpath(tmp_copy, project)],
            cwd=project, capture_output=True, text=True)
    finally:
        os.remove(tmp_copy)
    out = result.stdout + result.stderr
    if "Fraction satisfied" not in out:
        print("    (evaluator gave no score for this file; last lines below)")
        for line in out.strip().splitlines()[-5:]:
            print("     ", line)
    if "Fraction satisfied" in out:
        out = out.split("Fraction satisfied")[-1]
    found = {}
    for name, value in re.findall(r"Robustness for `(\w+)`\s*=\s*([-+0-9.eEinfa]+)", out):
        try:
            found[name] = float(value) >= 1.0
        except ValueError:
            pass
    return found


# ----------------------------------------------------------------------
# Drawing helpers
# ----------------------------------------------------------------------
def text(img, s, x, y, size=0.6, colour=TEXT, bold=False, font=None):
    cv2.putText(img, s, (int(x), int(y)), font or (FONT if bold else FONT_LIGHT),
                size, colour, 2 if bold else 1, cv2.LINE_AA)


def text_width(s, size=0.6, bold=False):
    (w, _), _ = cv2.getTextSize(s, FONT if bold else FONT_LIGHT, size, 2 if bold else 1)
    return w


def rounded_rect(img, x1, y1, x2, y2, colour, radius=10, filled=True):
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    r = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), colour, -1)
    cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), colour, -1)
    for cx, cy in [(x1 + r, y1 + r), (x2 - r, y1 + r), (x1 + r, y2 - r), (x2 - r, y2 - r)]:
        cv2.circle(img, (cx, cy), r, colour, -1, cv2.LINE_AA)
    if not filled:
        rounded_rect(img, x1 + 2, y1 + 2, x2 - 2, y2 - 2, BG, max(1, radius - 2))


def card(img, x, y, w, h, label, value, unit="", value_colour=TEXT):
    rounded_rect(img, x, y, x + w, y + h, PANEL)
    text(img, label, x + 14, y + 24, 0.45, DIM)
    text(img, value, x + 14, y + h - 18, 0.95, value_colour, bold=True)
    if unit:
        text(img, unit, x + 20 + text_width(value, 0.95, True), y + h - 18, 0.45, DIM)


def gap_colour(gap):
    if not math.isfinite(gap):
        return DIM
    if gap < 8:
        return RED
    if gap < 15:
        return AMBER
    return TEXT


class Chart:
    """A small time chart drawn once, with a cursor added per frame."""

    def __init__(self, x, y, w, h, t_max, y_max, title, y_unit):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.left, self.right = x + 46, x + w - 12
        self.top, self.bottom = y + 30, y + h - 26
        self.t_max, self.y_max = t_max, y_max
        self.title, self.y_unit = title, y_unit

    def px(self, t):
        return int(self.left + (self.right - self.left) * min(t, self.t_max) / self.t_max)

    def py(self, v):
        v = max(0.0, min(v, self.y_max))
        return int(self.bottom - (self.bottom - self.top) * v / self.y_max)

    def frame(self, img):
        rounded_rect(img, self.x, self.y, self.x + self.w, self.y + self.h, PANEL)
        text(img, self.title, self.x + 14, self.y + 21, 0.5, TEXT, bold=True)
        for frac in (0.0, 0.5, 1.0):
            v = self.y_max * frac
            yy = self.py(v)
            cv2.line(img, (self.left, yy), (self.right, yy), GRID, 1)
            text(img, f"{v:.0f}", self.x + 10, yy + 5, 0.4, DIM)
        for t in range(0, int(self.t_max) + 1, 5):
            xx = self.px(t)
            text(img, f"{t}s", xx - 8, self.bottom + 18, 0.4, DIM)

    def line(self, img, ts, vs, colour, thickness=2):
        points = []
        for t, v in zip(ts, vs):
            if math.isfinite(v):
                points.append((self.px(t), self.py(v)))
            elif len(points) > 1:
                cv2.polylines(img, [np.array(points)], False, colour, thickness, cv2.LINE_AA)
                points = []
            else:
                points = []
        if len(points) > 1:
            cv2.polylines(img, [np.array(points)], False, colour, thickness, cv2.LINE_AA)

    def hline(self, img, v, colour, label):
        yy = self.py(v)
        for xx in range(self.left, self.right, 12):
            cv2.line(img, (xx, yy), (min(xx + 6, self.right), yy), colour, 1, cv2.LINE_AA)
        text(img, label, self.left + 6, yy - 5, 0.4, colour)

    def band(self, img, v1, v2, colour):
        overlay = img.copy()
        cv2.rectangle(overlay, (self.left, self.py(v2)), (self.right, self.py(v1)), colour, -1)
        cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)

    def cursor(self, img, t):
        xx = self.px(t)
        cv2.line(img, (xx, self.top), (xx, self.bottom), TEXT, 1, cv2.LINE_AA)


def road_view(img, x, y, w, h, gap, ego_speed, lead_speed):
    """Top-down sketch of the lane: my car on the left, lead car ahead."""
    rounded_rect(img, x, y, x + w, y + h, PANEL)
    text(img, "Top view (not to scale vertically)", x + 14, y + 22, 0.45, DIM)
    road_top, road_bottom = y + 36, y + h - 30
    cv2.rectangle(img, (x + 14, road_top), (x + w - 14, road_bottom), (60, 56, 54), -1)
    mid = (road_top + road_bottom) // 2
    cv2.line(img, (x + 14, road_top), (x + w - 14, road_top), (210, 210, 210), 2)
    cv2.line(img, (x + 14, road_bottom), (x + w - 14, road_bottom), (210, 210, 210), 2)

    view_m = 70.0
    ego_x = x + 40
    scale = (w - 120) / view_m
    car_len, car_h = int(4.8 * scale), int((road_bottom - road_top) * 0.42)

    # 40 m marker and the 8 m "keep clear" zone
    m40 = int(ego_x + 40 * scale)
    for yy in range(road_top + 4, road_bottom - 4, 10):
        cv2.line(img, (m40, yy), (m40, yy + 5), AMBER, 1)
    text(img, "40 m", m40 - 16, road_top - 6, 0.4, AMBER)
    overlay = img.copy()
    cv2.rectangle(overlay, (ego_x + car_len // 2, mid - car_h // 2),
                  (int(ego_x + 8 * scale), mid + car_h // 2), RED, -1)
    cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)

    def draw_car(cx, colour, label):
        rounded_rect(img, cx - car_len // 2, mid - car_h // 2, cx + car_len // 2, mid + car_h // 2,
                     colour, radius=6)
        text(img, label, max(x + 14, cx - text_width(label, 0.4) // 2), road_bottom + 18, 0.4, colour)

    draw_car(ego_x, EGO_COLOUR, f"me {ego_speed:.1f} m/s")
    if not math.isfinite(gap):
        text(img, "lead car left the lane", ego_x + 120, mid + 6, 0.6, DIM, bold=True)
        return
    lead_x = int(ego_x + gap * scale)
    if gap > view_m:
        edge = x + w - 30
        cv2.arrowedLine(img, (edge - 40, mid), (edge, mid), LEAD_COLOUR, 2, cv2.LINE_AA, tipLength=0.4)
        text(img, f"lead {gap:.0f} m ahead", edge - 170, mid - car_h // 2 - 8, 0.45, LEAD_COLOUR)
        return
    draw_car(lead_x, LEAD_COLOUR, f"lead {lead_speed:.1f}")
    # gap arrow between the cars
    a, b = ego_x + car_len // 2 + 2, lead_x - car_len // 2 - 2
    if b - a > 20:
        yy = mid + 6
        cv2.arrowedLine(img, (a, yy), (b, yy), gap_colour(gap), 1, cv2.LINE_AA, tipLength=0.05)
        cv2.arrowedLine(img, (b, yy), (a, yy), gap_colour(gap), 1, cv2.LINE_AA, tipLength=0.05)
        label = f"{gap:.1f} m"
        if b - a > text_width(label, 0.45) + 10:
            text(img, label, (a + b) // 2 - text_width(label, 0.45) // 2, yy - 6, 0.45, gap_colour(gap))


# ----------------------------------------------------------------------
# Building one scenario video
# ----------------------------------------------------------------------
def build_background(name, rows, results):
    """Everything that doesn't change from frame to frame."""
    bg = np.full((H, W, 3), BG, np.uint8)
    text(bg, "Adaptive Cruise Control", 24, 38, 0.85, TEXT, bold=True)
    text(bg, "CSCI 513  |  Mini-Project 1  |  CARLA 0.9.15", 24 + text_width("Adaptive Cruise Control", 0.85, True) + 18,
         38, 0.5, DIM)
    text(bg, name, W - 24 - text_width(name, 0.8, True), 38, 0.8, AMBER, bold=True)

    t = [r["t"] for r in rows]
    t_max = max(20.0, t[-1] if t else 20.0)

    speed_chart = Chart(608, 300, 648, 170, t_max, 25.0, "Speed (m/s)", "m/s")
    speed_chart.frame(bg)
    speed_chart.band(bg, 18, 22, GREEN)
    speed_chart.hline(bg, 20, GREEN, "target 20")
    speed_chart.line(bg, t, [r["lead"] for r in rows], LEAD_COLOUR, 1)
    speed_chart.line(bg, t, [r["v"] for r in rows], EGO_COLOUR, 2)
    text(bg, "me", 608 + 470, 321, 0.42, EGO_COLOUR, bold=True)
    text(bg, "lead", 608 + 510, 321, 0.42, LEAD_COLOUR, bold=True)
    text(bg, "18-22 band", 608 + 560, 321, 0.42, GREEN)

    finite = [r["d"] for r in rows if math.isfinite(r["d"])]
    gap_max = max(50.0, min(120.0, (max(finite) if finite else 50.0) * 1.1))
    gap_chart = Chart(608, 482, 648, 170, t_max, gap_max, "Gap to lead car (m)", "m")
    gap_chart.frame(bg)
    overlay = bg.copy()
    for r0, r1 in zip(rows, rows[1:]):
        if r0["mode"] == 1:
            cv2.rectangle(overlay, (gap_chart.px(r0["t"]), gap_chart.top),
                          (gap_chart.px(r1["t"]), gap_chart.bottom), BLUE, -1)
    cv2.addWeighted(overlay, 0.12, bg, 0.88, 0, bg)
    gap_chart.hline(bg, 40, AMBER, "40 m")
    gap_chart.hline(bg, 4, RED, "4 m limit")
    gap_chart.line(bg, t, [r["d"] for r in rows], TEXT, 2)
    text(bg, "shaded = FOLLOWING", 608 + 470, 503, 0.42, BLUE)

    # Rule results along the bottom
    x = 24
    text(bg, "Grader:", x, H - 22, 0.5, DIM)
    x += 78
    for key, label in RULES:
        passed = results.get(key)
        colour = GREEN if passed else (RED if passed is False else DIM)
        tag = "PASS" if passed else ("FAIL" if passed is False else "--")
        s = f"{label}: {tag}"
        w = text_width(s, 0.45, True) + 24
        rounded_rect(bg, x, H - 44, x + w, H - 12, colour, radius=12, filled=False)
        text(bg, s, x + 12, H - 22, 0.45, colour, bold=True)
        x += w + 10
    return bg, speed_chart, gap_chart


def render_frame(background, speed_chart, gap_chart, row, cam, t_max,
                 main_label="DASHCAM", inset=None, inset_label="DASHCAM", legend=False):
    img = background.copy()

    # Main picture on the left: the dashcam, or the CARLA bird's-eye view
    cam_x, cam_y, cam_size = 24, 64, 560
    if cam is not None:
        img[cam_y:cam_y + cam_size, cam_x:cam_x + cam_size] = cv2.resize(cam, (cam_size, cam_size))
    else:
        rounded_rect(img, cam_x, cam_y, cam_x + cam_size, cam_y + cam_size, PANEL)
        text(img, "no video", cam_x + 220, cam_y + 280, 0.7, DIM)
    cv2.rectangle(img, (cam_x, cam_y), (cam_x + cam_size, cam_y + cam_size), GRID, 1)
    label_w = text_width(main_label, 0.5, True) + 24
    rounded_rect(img, cam_x + 10, cam_y + 10, cam_x + 10 + label_w, cam_y + 40, (0, 0, 0))
    text(img, main_label, cam_x + 22, cam_y + 31, 0.5, TEXT, bold=True)

    if legend:
        # Colour key for the bird's-eye view (CARLA draws my car red, others green)
        lx, ly = cam_x + 10, cam_y + 48
        rounded_rect(img, lx, ly, lx + 132, ly + 56, (0, 0, 0))
        cv2.rectangle(img, (lx + 12, ly + 12), (lx + 30, ly + 22), (60, 60, 255), -1)
        text(img, "my car", lx + 40, ly + 23, 0.45, TEXT)
        cv2.rectangle(img, (lx + 12, ly + 34), (lx + 30, ly + 44), (60, 255, 60), -1)
        text(img, "lead car", lx + 40, ly + 45, 0.45, TEXT)

    if inset is not None:
        size = 190
        ix, iy = cam_x + cam_size - size - 10, cam_y + cam_size - size - 10
        img[iy:iy + size, ix:ix + size] = cv2.resize(inset, (size, size))
        cv2.rectangle(img, (ix, iy), (ix + size, iy + size), TEXT, 1)
        rounded_rect(img, ix + 6, iy + 6, ix + 22 + text_width(inset_label, 0.42, True), iy + 30, (0, 0, 0))
        text(img, inset_label, ix + 14, iy + 24, 0.42, TEXT, bold=True)
    rounded_rect(img, cam_x + 10, cam_y + cam_size - 44, cam_x + 150, cam_y + cam_size - 10, (0, 0, 0))
    text(img, f"t = {row['t']:4.1f} s", cam_x + 22, cam_y + cam_size - 20, 0.6, TEXT, bold=True)

    # Timeline under the dashcam
    bar_y = cam_y + cam_size + 18
    rounded_rect(img, cam_x, bar_y, cam_x + cam_size, bar_y + 10, PANEL, radius=5)
    filled = int(cam_size * min(row["t"], t_max) / t_max)
    if filled > 10:
        rounded_rect(img, cam_x, bar_y, cam_x + filled, bar_y + 10, AMBER, radius=5)

    # Cards
    following = row["mode"] == 1
    mode_colour = BLUE if following else GREEN
    card(img, 608, 64, 150, 84, "MODE", "FOLLOW" if following else "CRUISE", "", mode_colour)
    card(img, 774, 64, 150, 84, "MY SPEED", f"{row['v']:.1f}", "m/s", EGO_COLOUR)
    card(img, 940, 64, 150, 84, "LEAD SPEED", f"{row['lead']:.1f}", "m/s", LEAD_COLOUR)
    gap_txt = f"{row['d']:.1f}" if math.isfinite(row["d"]) else "--"
    card(img, 1106, 64, 150, 84, "GAP", gap_txt, "m" if math.isfinite(row["d"]) else "", gap_colour(row["d"]))

    road_view(img, 608, 162, 648, 126, row["d"], row["v"], row["lead"])

    speed_chart.cursor(img, row["t"])
    gap_chart.cursor(img, row["t"])
    yy = gap_chart.py(row["d"]) if math.isfinite(row["d"]) else None
    if yy is not None:
        cv2.circle(img, (gap_chart.px(row["t"]), yy), 5, TEXT, -1, cv2.LINE_AA)
    cv2.circle(img, (speed_chart.px(row["t"]), speed_chart.py(row["v"])), 5, EGO_COLOUR, -1, cv2.LINE_AA)
    return img


def title_card(name, results):
    img = np.full((H, W, 3), BG, np.uint8)
    text(img, name, W // 2 - text_width(name, 1.6, True) // 2, H // 2 - 10, 1.6, AMBER, bold=True)
    passed = sum(1 for k, _ in RULES if results.get(k))
    s = f"{passed}/5 rules passed" if results else "Adaptive Cruise Control"
    text(img, s, W // 2 - text_width(s, 0.8) // 2, H // 2 + 40, 0.8, GREEN if passed == 5 else TEXT)
    return img


def to_browser_mp4(path):
    """Re-encode to H.264 (plays in browsers and on GitHub) if ffmpeg exists."""
    if not shutil.which("ffmpeg"):
        return False
    tmp = path + ".h264.mp4"
    result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp])
    if result.returncode == 0 and os.path.getsize(tmp) > 0:
        os.replace(tmp, path)
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def save_gif(frames, path, width=640, step=2):
    from PIL import Image
    images = []
    for frame in frames[::step]:
        small = cv2.resize(frame, (width, int(frame.shape[0] * width / frame.shape[1])),
                           interpolation=cv2.INTER_AREA)
        images.append(Image.fromarray(small[:, :, ::-1]).convert("P", palette=Image.ADAPTIVE, colors=128))
    images[0].save(path, save_all=True, append_images=images[1:], duration=int(1000 * step / FPS),
                   loop=0, optimize=True)


def make_hud_frames(name, rows, results, main_frames, main_label="DASHCAM",
                    inset_frames=None, inset_label="DASHCAM", legend=False):
    """Build every HUD frame for one scenario."""
    background, speed_chart, gap_chart = build_background(name, rows, results)
    t_max = max(20.0, rows[-1]["t"])

    def pick(frames, k):
        if not frames:
            return None
        return frames[min(len(frames) - 1, int(k * len(frames) / len(rows)))]

    return [render_frame(background, speed_chart, gap_chart, row, pick(main_frames, k), t_max,
                         main_label, pick(inset_frames, k), inset_label, legend)
            for k, row in enumerate(rows)]


def save_scenario(out, name, frames, results, all_writer, make_gif):
    """Write one scenario's video (and GIF), and add it to the combined video."""
    path = os.path.join(out, "videos", f"{name}.mp4")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for f in frames:
        writer.write(f)
    writer.release()
    to_browser_mp4(path)

    card_img = title_card(name, results)
    for _ in range(FPS):
        all_writer.write(card_img)
    for f in frames:
        all_writer.write(f)

    if make_gif:
        save_gif(frames, os.path.join(out, "gifs", f"{name}.gif"))


# ----------------------------------------------------------------------
def write_readme(out, names, results, has_gifs, birdeye=False):
    lines = [
        "# Adaptive Cruise Control in CARLA (CSCI 513 Mini-Project 1)",
        "",
        "An adaptive cruise controller for CARLA 0.9.15. Each step it works out the fastest speed",
        "from which the car could still stop behind the lead car (even if the lead brakes hard),",
        "aims for that or 20 m/s (whichever is lower), and sets throttle/brake from the speed error.",
        "It reports `CRUISING` when the lead car is more than 40 m away or out of the lane, otherwise `FOLLOWING`.",
        "",
        "## Results",
        "",
        "| Scenario | " + " | ".join(label for _, label in RULES) + " |",
        "|---|" + "---|" * len(RULES),
    ]
    totals = {k: 0 for k, _ in RULES}
    for name in names:
        r = results.get(name, {})
        cells = []
        for key, _ in RULES:
            if r.get(key):
                totals[key] += 1
                cells.append("PASS")
            elif key in r:
                cells.append("**FAIL**")
            else:
                cells.append("-")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if any(results.values()):
        n = len(names)
        lines.append("| **Fraction satisfied** | " + " | ".join(f"**{totals[k] / n:.2f}**" for k, _ in RULES) + " |")
    lines += [
        "",
        "## Scenario videos",
        "",
        ("Each video shows the CARLA bird's-eye view (my car red, lead car green) with the dashcam "
         "in the corner," if birdeye else "Each video shows the dashcam,"),
        "live speed/gap readouts, a simple lane sketch, speed and gap graphs, and the grader's verdict",
        "for each rule. Full-quality videos are in [`videos/`](videos/);",
        "all scenarios back to back: [`videos/all_scenarios.mp4`](videos/all_scenarios.mp4).",
        "",
    ]
    for name in names:
        lines.append(f"### {name}")
        lines.append("")
        if has_gifs:
            lines.append(f"[![{name}](gifs/{name}.gif)](videos/{name}.mp4)")
        else:
            lines.append(f"[{name}.mp4](videos/{name}.mp4)")
        lines.append("")
    lines += [
        "## Files",
        "",
        "- `controller.py`: the controller (the file submitted for grading)",
        "- `logs/`: raw traces from the simulator (one CSV per scenario)",
        "- `videos/`, `gifs/`: HUD videos and previews",
        "",
    ]
    with open(os.path.join(out, "README.md"), "w") as f:
        f.write("\n".join(lines))


def main():
    p = argparse.ArgumentParser(description="Make HUD videos and a GitHub folder for MP1")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--run-name", default="attempt01", help="which logs/<name> and vids/<name> to use")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--no-eval", action="store_true", help="skip the PASS/FAIL badges")
    p.add_argument("--no-gifs", action="store_true", help="skip the GIF previews")
    p.add_argument("--no-colour-fix", action="store_true", help="don't swap red/blue in the dashcam")
    args = p.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    out = os.path.abspath(os.path.expanduser(args.out))
    log_dir = os.path.join(project, "logs", args.run_name)
    vid_dir = os.path.join(project, "vids", args.run_name)
    csv_files = sorted(glob.glob(os.path.join(log_dir, "episode-*.csv")))
    if not csv_files:
        print(f"No logs found in {log_dir}. Check --run-name (e.g. attempt01).")
        sys.exit(1)

    for sub in ("videos", "gifs", "logs"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    controller = os.path.join(project, "mp1_controller", "controller.py")
    if os.path.isfile(controller):
        shutil.copy(controller, os.path.join(out, "controller.py"))

    print(f"Making videos for {len(csv_files)} scenarios from '{args.run_name}' -> {out}")
    all_path = os.path.join(out, "videos", "all_scenarios.mp4")
    all_writer = cv2.VideoWriter(all_path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    names, results = [], {}

    for i, csv_path in enumerate(csv_files, 1):
        name = os.path.basename(csv_path)[len("episode-"):-len(".csv")]
        names.append(name)
        shutil.copy(csv_path, os.path.join(out, "logs", f"{name}.csv"))
        rows = read_log(csv_path)
        cams = read_frames(os.path.join(vid_dir, f"episode-{name}.mp4"), not args.no_colour_fix)
        results[name] = {} if args.no_eval else rule_results(project, csv_path)
        passed = sum(1 for k, _ in RULES if results[name].get(k))
        print(f"  [{i:2d}/{len(csv_files)}] {name:14s} rules passed: "
              f"{passed if results[name] else '-'}  dashcam frames: {len(cams)}")

        frames = make_hud_frames(name, rows, results[name], cams)
        save_scenario(out, name, frames, results[name], all_writer, not args.no_gifs)

    all_writer.release()
    to_browser_mp4(all_path)
    write_readme(out, names, results, not args.no_gifs)
    print(f"\nDone. Open the folder with:  xdg-open {out}")
    print(f"Watch everything:           xdg-open {all_path}")


if __name__ == "__main__":
    main()
