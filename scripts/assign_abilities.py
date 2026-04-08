"""One-time script to assign abilities to all Pokemon in pokemon.json."""
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')

# Type-based default abilities (applied if no specific override)
TYPE_DEFAULTS = {
    "fire": "blaze",
    "water": "torrent",
    "grass": "overgrow",
    "electric": "static",
    "bug": "swarm",
    "poison": "immunity",
    "rock": "sturdy",
    "steel": "sturdy",
    "ground": "guts",
    "fighting": "guts",
    "ghost": "levitate",
    "psychic": "insomnia",
    "dark": "intimidate",
    "ice": "thick_fat",
    "dragon": "multiscale",
    "flying": "keen_eye",
    "fairy": "limber",
    "normal": "guts",
}

# Specific overrides for iconic Pokemon (dex_id -> ability)
OVERRIDES = {
    # Gen 1 starters
    1: "overgrow", 2: "overgrow", 3: "overgrow",
    4: "blaze", 5: "blaze", 6: "blaze",
    7: "torrent", 8: "torrent", 9: "torrent",
    # Pikachu line
    25: "static", 26: "static", 172: "static",
    # Geodude line
    74: "sturdy", 75: "sturdy", 76: "sturdy",
    # Gastly line
    92: "levitate", 93: "levitate", 94: "levitate",
    # Onix / Steelix
    95: "sturdy", 208: "sturdy",
    # Machop line
    66: "guts", 67: "guts", 68: "guts",
    # Abra line
    63: "synchronize", 64: "synchronize", 65: "synchronize",
    # Magnemite line
    81: "sturdy", 82: "sturdy",
    # Snorlax / Munchlax
    143: "thick_fat", 446: "thick_fat",
    # Gyarados
    130: "intimidate",
    # Dragonite line
    147: "shed_skin", 148: "shed_skin", 149: "multiscale",
    # Mewtwo
    150: "pressure",
    # Mew
    151: "synchronize",
    # Arcanine
    59: "intimidate",
    # Vulpix / Ninetales
    37: "flash_fire", 38: "flash_fire",
    # Growlithe
    58: "intimidate",
    # Jolteon / Vaporeon / Flareon
    134: "water_veil", 135: "static", 136: "flash_fire",
    # Eevee
    133: "adaptability", 196: "synchronize", 197: "synchronize",
    470: "overgrow", 471: "thick_fat", 700: "limber",
    # Ditto
    132: "limber",
    # Lapras
    131: "water_veil",
    # Hitmonlee / Hitmonchan / Hitmontop
    106: "limber", 107: "guts", 237: "intimidate",
    # Chansey / Blissey
    113: "natural_cure", 242: "natural_cure",
    # Scyther / Scizor
    123: "swarm", 212: "swarm",
    # Pinsir
    127: "mold_breaker",
    # Magikarp
    129: "run_away",
    # Aerodactyl
    142: "pressure",
    # Articuno / Zapdos / Moltres
    144: "pressure", 145: "static", 146: "flame_body",
    # Gen 2 starters
    152: "overgrow", 153: "overgrow", 154: "overgrow",
    155: "blaze", 156: "blaze", 157: "blaze",
    158: "torrent", 159: "torrent", 160: "torrent",
    # Ampharos
    179: "static", 180: "static", 181: "static",
    # Marill / Azumarill
    183: "huge_power", 184: "huge_power",
    # Wobbuffet
    202: "shed_skin",
    # Heracross
    214: "guts",
    # Skarmory
    227: "sturdy",
    # Houndour / Houndoom
    228: "flash_fire", 229: "flash_fire",
    # Slugma / Magcargo
    218: "flame_body", 219: "flame_body",
    # Tyranitar
    248: "intimidate",
    # Lugia / Ho-Oh
    249: "multiscale", 250: "pressure",
    # Celebi
    251: "natural_cure",
    # Legendary beasts
    243: "pressure", 244: "pressure", 245: "pressure",
    # Gen 3 starters
    252: "overgrow", 253: "overgrow", 254: "overgrow",
    255: "blaze", 256: "blaze", 257: "speed_boost",
    258: "torrent", 259: "torrent", 260: "torrent",
    # Ralts line
    280: "synchronize", 281: "synchronize", 282: "synchronize",
    # Slakoth line
    287: "guts", 288: "speed_boost", 289: "guts",
    # Ninjask / Shedinja
    291: "speed_boost", 292: "sturdy",
    # Mawile
    303: "intimidate",
    # Flygon line
    328: "levitate", 329: "levitate", 330: "levitate",
    # Salamence
    373: "intimidate",
    # Metagross line
    374: "pressure", 375: "pressure", 376: "pressure",
    # Groudon / Kyogre / Rayquaza
    382: "pressure", 383: "pressure", 384: "pressure",
    # Gen 4 starters
    387: "overgrow", 388: "overgrow", 389: "overgrow",
    390: "blaze", 391: "blaze", 392: "blaze",
    393: "torrent", 394: "torrent", 395: "torrent",
    # Luxray line
    403: "intimidate", 404: "intimidate", 405: "intimidate",
    # Lucario
    448: "guts",
    # Garchomp line
    443: "guts", 444: "guts", 445: "guts",
    # Rotom
    479: "levitate",
    # Dialga / Palkia / Giratina
    483: "pressure", 484: "pressure", 487: "levitate",
    # Gen 5 starters
    495: "overgrow", 496: "overgrow", 497: "overgrow",
    498: "blaze", 499: "blaze", 500: "blaze",
    501: "torrent", 502: "torrent", 503: "torrent",
    # Excadrill
    530: "mold_breaker",
    # Haxorus
    612: "mold_breaker",
    # Volcarona
    637: "flame_body",
    # Gen 6 starters
    650: "overgrow", 651: "overgrow", 652: "overgrow",
    653: "blaze", 654: "blaze", 655: "blaze",
    656: "torrent", 657: "torrent", 658: "torrent",
    # Gen 7 starters
    722: "overgrow", 723: "overgrow", 724: "overgrow",
    725: "blaze", 726: "blaze", 727: "blaze",
    728: "torrent", 729: "torrent", 730: "torrent",
    # Gen 8 starters
    810: "overgrow", 811: "overgrow", 812: "overgrow",
    813: "blaze", 814: "blaze", 815: "blaze",
    816: "torrent", 817: "torrent", 818: "torrent",
    # Gen 9 starters
    906: "overgrow", 907: "overgrow", 908: "overgrow",
    909: "blaze", 910: "blaze", 911: "blaze",
    912: "torrent", 913: "torrent", 914: "torrent",
    # Staraptor line
    396: "intimidate", 397: "intimidate", 398: "intimidate",
    # Slowbro / Slowking
    79: "regenerator", 80: "regenerator", 199: "regenerator",
    # Magmar / Magmortar
    126: "flame_body", 467: "flame_body",
    # Electabuzz / Electivire
    125: "static", 466: "static",
    # Breloom
    286: "poison_heal",
    # Gliscor
    472: "poison_heal",
    # Toxapex
    747: "regenerator", 748: "regenerator",
    # Weezing
    110: "levitate",
    # Bronzong
    437: "levitate",
}


def main():
    pokemon_path = os.path.join(DATA_DIR, 'pokemon.json')
    with open(pokemon_path, encoding='utf-8') as f:
        pokemon_list = json.load(f)

    override_count = 0
    default_count = 0

    for p in pokemon_list:
        dex_id = p["id"]
        if dex_id in OVERRIDES:
            p["ability"] = OVERRIDES[dex_id]
            override_count += 1
        else:
            primary_type = p["types"][0]
            p["ability"] = TYPE_DEFAULTS.get(primary_type, "pressure")
            default_count += 1

    with open(pokemon_path, 'w', encoding='utf-8') as f:
        json.dump(pokemon_list, f, ensure_ascii=False, separators=(',', ':'))

    print(f"Assigned abilities to {len(pokemon_list)} Pokemon")
    print(f"  Specific overrides: {override_count}")
    print(f"  Type defaults: {default_count}")

    # Verify all abilities reference valid ability IDs
    abilities_path = os.path.join(DATA_DIR, 'abilities.json')
    with open(abilities_path, encoding='utf-8') as f:
        abilities = json.load(f)

    missing = set()
    for p in pokemon_list:
        ab = p.get("ability")
        if ab and ab not in abilities:
            missing.add(ab)
    if missing:
        print(f"  WARNING: {len(missing)} ability IDs not in abilities.json: {missing}")
    else:
        print("  All ability IDs valid!")


if __name__ == "__main__":
    main()
