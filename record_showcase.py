#!/usr/bin/env python
"""
record_showcase.py  --  run every scenario and record the CARLA bird's-eye view.

It drives CARLA exactly like `python -m mp1_simulator` does (same simulator code,
same controller, same scenario files, same log format). Every step it also grabs:
  * the CARLA bird's-eye map from the project's pygame renderer
    (both cars move: positions are updated every step)
  * the dashcam image
Then make_showcase.py turns that into HUD videos + GIFs + README.

No assignment files are changed. Put this file next to make_showcase.py.

Usage (project environment active, CARLA server running in another terminal, from the repo root):
  python record_showcase.py
  python record_showcase.py --show        # also open a live window
  python record_showcase.py --only scenario3_5
"""

import argparse
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_showcase as hud  # noqa: E402  (drawing code lives there)

DEFAULT_PROJECT = hud.DEFAULT_PROJECT
DEFAULT_OUT = hud.DEFAULT_OUT


def main():
    p = argparse.ArgumentParser(description="Record bird's-eye HUD videos for MP1")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--only", nargs="*", help="scenario names to run, e.g. scenario3_5 (default: all)")
    p.add_argument("--view-range", type=float, default=70.0,
                   help="metres shown top-to-bottom in the bird's-eye view (default 70)")
    p.add_argument("--show", action="store_true", help="also show a live pygame window")
    p.add_argument("--no-gifs", action="store_true")
    args = p.parse_args()

    if not args.show:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")   # draw off-screen

    project = os.path.abspath(os.path.expanduser(args.project))
    out = os.path.abspath(os.path.expanduser(args.out))
    sys.path.insert(0, project)

    import carla
    import cv2
    import numpy as np
    import pygame
    from mp1_simulator.simulator import CONFIG, Mode, Simulator
    from mp1_simulator.render import BirdeyeRender
    from mp1_controller.controller import Controller

    scenarios = sorted(glob.glob(os.path.join(project, "test_data", "*.json")))
    if args.only:
        scenarios = [s for s in scenarios if os.path.basename(s)[:-5] in args.only]
    if not scenarios:
        print("No scenario files matched.")
        sys.exit(1)

    for sub in ("videos", "gifs", "logs"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    controller_file = os.path.join(project, "mp1_controller", "controller.py")
    hud.shutil.copy(controller_file, os.path.join(out, "controller.py"))

    # Load Town06 patiently first; the simulator itself only waits 10 seconds.
    print("Connecting to CARLA and loading Town06 (can take a minute the first time)...")
    client = carla.Client("127.0.0.1", CONFIG["port"])
    client.set_timeout(180.0)
    client.load_world(CONFIG["town"])

    sim = None
    for attempt in range(3):
        try:
            sim = Simulator(render=False)
            break
        except RuntimeError as error:
            print(f"  simulator connect attempt {attempt + 1} failed ({error}); retrying...")
    if sim is None:
        print("Could not start the simulator. Is the CARLA server running?")
        sys.exit(1)

    # Our own bird's-eye renderer (same class the simulator uses for its pygame window).
    size = 560
    pixels_per_meter = size / args.view_range
    params = {
        "screen_size": [size, size],
        "pixels_per_meter": pixels_per_meter,
        "pixels_ahead_vehicle": (args.view_range / 2 - CONFIG["d_behind"]) * pixels_per_meter,
    }
    pygame.init()
    display = pygame.display.set_mode((size, size))
    pygame.display.set_caption("MP1 bird's-eye recorder")
    print("Building the bird's-eye map image (one-time, ~10-60 s)...")
    renderer = BirdeyeRender(sim.world, params)

    def grab():
        """Draw the bird's-eye view for the current moment and return (birdeye, dashcam) as BGR."""
        history.append(sim._get_actor_polygons("vehicle.*"))
        del history[:-6]                       # short fading trail behind each car
        renderer.vehicle_polygons = history
        renderer.render(display, ["roadmap", "actors"])
        if args.show:
            pygame.display.flip()
            pygame.event.pump()
        bird = pygame.surfarray.array3d(display).swapaxes(0, 1)[:, :, ::-1].copy()
        cam = sim.camera_img[:, :, ::-1].copy()   # simulator keeps RGB; OpenCV wants BGR
        return bird, cam

    all_path = os.path.join(out, "videos", "all_scenarios.mp4")
    all_writer = cv2.VideoWriter(all_path, cv2.VideoWriter_fourcc(*"mp4v"), hud.FPS, (hud.W, hud.H))
    names, results = [], {}

    try:
        for i, scenario_file in enumerate(scenarios, 1):
            name = os.path.basename(scenario_file)[:-5]
            names.append(name)
            with open(scenario_file) as f:
                data = json.load(f)

            sim._set_ado_control(data["ado_actions"])
            sim.set_spawn_points(data["ego"], data["lead"])
            controller = Controller(distance_threshold=CONFIG["distance_threshold"])

            # --- one episode, same order of events as mp1_simulator/__main__.py ---
            obs = sim.reset()
            sim.camera_buffer = []
            renderer.set_hero(sim.ego, sim.ego.id)
            history = []
            trace = [(obs, sim._get_ado_velocity(), Mode.CRUISING)]
            birds, cams = [], []
            b, c = grab()
            birds.append(b)
            cams.append(c)
            while True:
                action, mode = controller.run_step(obs)
                obs = sim.step(action)
                trace.append((obs, sim._get_ado_velocity(), mode))
                b, c = grab()
                birds.append(b)
                cams.append(c)
                sim.camera_buffer = []
                if sim.completed:
                    break

            # --- save the log in the official format and score it ---
            csv_path = os.path.join(out, "logs", f"{name}.csv")
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestep", "time_elapsed", "ego_velocity", "desired_speed",
                            "distance_to_lead", "lead_speed", "mode"])
                for k, (o, lead_v, m) in enumerate(trace):
                    w.writerow([k, sim.dt * k, o.ego_velocity, o.desired_speed,
                                o.distance_to_lead, lead_v, m.value])
            results[name] = hud.rule_results(project, csv_path)
            passed = sum(1 for key, _ in hud.RULES if results[name].get(key))
            print(f"  [{i:2d}/{len(scenarios)}] {name:14s} steps: {len(trace) - 1:3d}  "
                  f"rules passed: {passed}/5")

            # --- turn it into the HUD video ---
            rows = hud.read_log(csv_path)
            frames = hud.make_hud_frames(name, rows, results[name], birds,
                                         main_label="CARLA BIRD'S-EYE VIEW",
                                         inset_frames=cams, inset_label="DASHCAM", legend=True)
            hud.save_scenario(out, name, frames, results[name], all_writer, not args.no_gifs)
    finally:
        all_writer.release()
        try:
            sim._set_synchronous_mode(False)   # hand the server back in normal mode
            sim._clear_all_actors()
        except Exception:
            pass
        pygame.quit()

    hud.to_browser_mp4(all_path)
    hud.write_readme(out, names, results, not args.no_gifs, birdeye=True)
    print(f"\nDone. Folder: {out}")
    print(f"Watch everything: xdg-open {all_path}")


if __name__ == "__main__":
    main()
