"""Digests of every frame ``--dump-frames`` draws, for the golden check in tests/test_frame_golden.py (#32).

One digest for each theme, variant and sample state. It hashes, in this order, the floating widget at each scale, the
taskbar strip at each height and scale, the hover card at each scale and the tray icon at each size. The sample states
use a fixed clock (dump.SAMPLE_NOW), and the digests are worked out in a fresh Python with the time zone pinned to UTC
(reset times are drawn in local time), so they are the same on any PC.

Record a release's digests after its tag, from the root of this repo, with a copy of the release in a folder outside it:

    git worktree add ../frames-v2.3.0 v2.3.0
    python tests/frame_digests.py --repo ../frames-v2.3.0 --ref v2.3.0 --out tests/fixtures/frame_digests.json
    git worktree remove ../frames-v2.3.0

Then empty CHANGED in tests/test_frame_golden.py. ``--print`` (used by collect) prints the digests of the code that
Python imports, as JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PINNED_TZ = "UTC0"                   # the C runtime reads TZ when Python starts, so it is set for a fresh process
PARTS = max(1, min(4, (os.cpu_count() or 1) - 1))   # processes that share the rendering


def digests(part: int = 0, parts: int = 1) -> dict[str, str]:
    """The digests of the usage_monitor this Python imports, or the ``part``-th of ``parts`` shares of them. Reset
    times follow the local time zone: see collect."""
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    loaded = dump.load_theme_objects()
    found: dict[str, str] = {}
    index = -1
    for theme_id in sorted(loaded):
        theme, skin = loaded[theme_id], get_skin(theme_id)
        for variant in theme.variants:
            for state in dump.DUMP_STATE_NAMES:
                index += 1
                if index % parts != part:
                    continue
                model = dump.sample_model(state)
                hashed = hashlib.sha256()
                for scale in dump.SCALES:
                    _feed(hashed, skin.render_floating(model, theme, variant, scale, None))
                    for height in dump.TASKBAR_HEIGHTS_AT_1X:
                        _feed(hashed, skin.render_taskbar(model, theme, variant, round(height * scale), scale, None))
                    _feed(hashed, skin.render_tooltip(model, theme, variant, scale))
                for size in dump.TRAY_SIZES:
                    _feed(hashed, skin.render_tray_icon(model, theme, variant, size))
                found[f"{theme_id}/{variant.id}/{state}"] = hashed.hexdigest()[:24]
    return found


def _feed(hashed: Any, image: Any) -> None:
    hashed.update(f"{image.mode} {image.width}x{image.height};".encode("ascii"))
    hashed.update(image.tobytes())


def collect(repo: str | Path) -> dict[str, str]:
    """The digests of the code in ``repo``, worked out in a fresh Python with the time zone pinned. Raises when that
    Python imported usage_monitor from anywhere else."""
    root = Path(repo).resolve()
    env = {**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(root)}
    runs = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--print", "--part", f"{part}/{PARTS}"],
                             cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for part in range(PARTS)]
    frames: dict[str, str] = {}
    for run in runs:
        out, err = run.communicate(timeout=1800)
        if run.returncode:
            raise RuntimeError(f"the frame digests could not be worked out:\n{err[-2000:]}")
        result = json.loads(out)
        source = Path(result["source"]).resolve()
        if root not in source.parents:
            raise RuntimeError(f"usage_monitor came from {source}, not from {root}")
        frames.update(result["frames"])
    return frames


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--print", action="store_true", help="print the digests of the code Python imports, as JSON")
    parser.add_argument("--part", default="0/1", help="with --print: print only this share, as PART/PARTS")
    parser.add_argument("--repo", help="the checkout to record (default: this one)")
    parser.add_argument("--ref", help="the release the checkout is, for the record")
    parser.add_argument("--out", help="where to write the record (default: print it)")
    args = parser.parse_args(argv)
    if args.print:
        import usage_monitor

        part, parts = (int(n) for n in args.part.split("/"))
        print(json.dumps({"source": usage_monitor.__file__, "frames": digests(part, parts)}))
        return 0
    repo = Path(args.repo) if args.repo else Path(__file__).resolve().parent.parent
    record = {"ref": args.ref or "unknown", "tz": PINNED_TZ, "frames": collect(repo)}
    text = json.dumps(record, indent=1, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {len(record['frames'])} digests of {record['ref']} to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
