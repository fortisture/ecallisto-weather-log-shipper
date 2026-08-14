#!/usr/bin/env python3
"""Reorganise the local data stores into year/month/day folders.

Older versions kept spectrograms in flat per-day folders named
"YYYY-MM-DD" and weather logs loose at the root. Both stores are now laid
out as <store>/YYYY/MM/DD/<file>, mirroring the e-Callisto archive.

This moves existing files into the new layout. It is safe to re-run: a
file already in the right place is left alone, and nothing is overwritten
or deleted -- a name collision is reported and skipped.

    python migrate_store_layout.py             # show what would move
    python migrate_store_layout.py --apply     # actually move
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "station"))

from receiver import date_subdir, UNDATED_DIR  # noqa: E402


def already_nested(root, path):
    """True if the file already sits at <root>/YYYY/MM/DD/<name>."""
    relative = os.path.relpath(path, root).replace("\\", "/").split("/")
    if len(relative) != 4:
        return False
    year, month, day, _ = relative
    return (
        len(year) == 4 and year.isdigit()
        and len(month) == 2 and month.isdigit()
        and len(day) == 2 and day.isdigit()
    )


def plan_moves(root):
    moves = []
    if not os.path.isdir(root):
        return moves

    for current, _, files in os.walk(root):
        for name in files:
            path = os.path.join(current, name)

            if already_nested(root, path):
                continue
            if name.endswith((".tmp", ".part")):
                continue

            parts = date_subdir(name)
            if parts:
                target_dir = os.path.join(root, *parts)
            else:
                # No date in the filename: try the folder it sits in, which
                # in the old layout was itself the date.
                folder = os.path.basename(current)
                folder_parts = date_subdir(folder)
                target_dir = os.path.join(root, *folder_parts) if folder_parts \
                    else os.path.join(root, UNDATED_DIR)

            target = os.path.join(target_dir, name)
            if os.path.abspath(target) != os.path.abspath(path):
                moves.append((path, target))

    return moves


def prune_empty(root):
    """Remove directories left empty by the move, deepest first."""
    removed = 0
    for current, dirs, files in os.walk(root, topdown=False):
        if current == root:
            continue
        if not files and not os.listdir(current):
            try:
                os.rmdir(current)
                removed += 1
            except OSError:
                pass
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="perform the moves (default: dry run)")
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    total = 0

    for store in ("weather", "fits"):
        root = os.path.join(data_dir, store)
        moves = plan_moves(root)
        print(f"\n{store}: {len(moves)} file(s) to move  [{root}]")

        for source, target in moves[:5]:
            print(f"  {os.path.relpath(source, root)}"
                  f"  ->  {os.path.relpath(target, root)}")
        if len(moves) > 5:
            print(f"  ... and {len(moves) - 5} more")

        if args.apply:
            moved = skipped = 0
            for source, target in moves:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    print(f"  SKIP (target exists): {os.path.basename(target)}")
                    skipped += 1
                    continue
                shutil.move(source, target)
                moved += 1

            pruned = prune_empty(root)
            print(f"  moved {moved}, skipped {skipped}, removed {pruned} empty folder(s)")

        total += len(moves)

    if not args.apply:
        print(f"\nDry run -- {total} file(s) would move. Re-run with --apply to do it.")


if __name__ == "__main__":
    main()
