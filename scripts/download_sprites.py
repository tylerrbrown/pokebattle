#!/usr/bin/env python3
"""Download Pokemon sprites from PokeAPI GitHub repository.

Usage:
  python download_sprites.py                       # Default: 1-251
  python download_sprites.py --start 252 --end 386 # Gen 3 only

Source: PokeAPI sprites repo.
"""

import argparse
import os
import sys
import time
import urllib.request
import urllib.error

SPRITES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sprites")
FRONT_DIR = os.path.join(SPRITES_DIR, "front")
BACK_DIR = os.path.join(SPRITES_DIR, "back")

# Use the PokeAPI sprites GitHub repo - these are the classic Gen 1 sprites
# We'll use generation-v/black-white for better quality pixel sprites (96x96)
# that still look retro but are cleaner than the original 56x56 Game Boy sprites
FRONT_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-v/black-white/{id}.png"
BACK_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-v/black-white/back/{id}.png"

# Fallback: default sprites (larger, modern, good quality)
FRONT_FALLBACK = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{id}.png"
BACK_FALLBACK = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/back/{id}.png"


def download_file(url, dest_path, retries=3):
    """Download a file with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PokeBattle-SpriteDownloader/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                with open(dest_path, "wb") as f:
                    f.write(data)
                return len(data)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return None
    return None


def main():
    parser = argparse.ArgumentParser(description="Download Pokemon sprites")
    parser.add_argument("--start", type=int, default=1, help="Starting dex ID (default: 1)")
    parser.add_argument("--end", type=int, default=251, help="Ending dex ID (default: 251)")
    args = parser.parse_args()

    os.makedirs(FRONT_DIR, exist_ok=True)
    os.makedirs(BACK_DIR, exist_ok=True)

    success = 0
    failed = []

    for dex_id in range(args.start, args.end + 1):
        name = f"{dex_id:03d}.png"
        front_path = os.path.join(FRONT_DIR, name)
        back_path = os.path.join(BACK_DIR, name)

        # Skip if already downloaded
        if os.path.exists(front_path) and os.path.exists(back_path):
            print(f"#{dex_id:03d} - already exists, skipping")
            success += 1
            continue

        print(f"#{dex_id:03d} - downloading...", end=" ", flush=True)

        # Download front sprite
        front_url = FRONT_URL.format(id=dex_id)
        front_size = download_file(front_url, front_path)
        if front_size is None:
            # Try fallback
            front_url = FRONT_FALLBACK.format(id=dex_id)
            front_size = download_file(front_url, front_path)

        # Download back sprite
        back_url = BACK_URL.format(id=dex_id)
        back_size = download_file(back_url, back_path)
        if back_size is None:
            # Try fallback
            back_url = BACK_FALLBACK.format(id=dex_id)
            back_size = download_file(back_url, back_path)

        if front_size and back_size:
            print(f"OK (front={front_size}B, back={back_size}B)")
            success += 1
        else:
            print("FAILED")
            failed.append(dex_id)

        # Rate limit
        if dex_id % 20 == 0:
            time.sleep(1)

    print(f"\nDone! {success}/151 downloaded successfully.")
    if failed:
        print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
