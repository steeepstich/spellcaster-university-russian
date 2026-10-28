from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import traceback
from pathlib import Path

import UnityPy


APP_NAME = "Spellcaster University — русификатор"
ORIGINAL_SHA256 = "DDE8696DE7B61800F52C1137F2BDDE5DFE01D6F7E90457AB2CE78F663D357549"
RUSSIAN_SHA256 = "5CA9918AEB1EA6F6FEFF6BB6669A36248E0DF1B17D8198E40EFD6CB67DA02120"
ASSET_RELATIVE = Path("Spellcaster University_Data") / "resources.assets"
BACKUP_RELATIVE = Path("_Russian_Backup") / "resources.assets.original"


def bundled_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def align4(data: bytearray) -> None:
    while len(data) % 4:
        data.append(0)


def write_i32(data: bytearray, value: int) -> None:
    data.extend(struct.pack("<i", value))


def write_string(data: bytearray, value: str) -> None:
    raw = value.encode("utf-8")
    write_i32(data, len(raw))
    data.extend(raw)
    align4(data)


def write_byte_array(data: bytearray, values: list[int]) -> None:
    write_i32(data, len(values))
    data.extend(values)
    align4(data)


def game_is_running() -> bool:
    if os.environ.get("RU_INSTALLER_TEST_ALLOW_RUNNING") == "1":
        return False
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Spellcaster University.exe", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "Spellcaster University.exe".lower() in result.stdout.lower()


def normalize_game_root(value: str | Path) -> Path:
    path = Path(str(value).strip().strip('"')).expanduser()
    if path.name.lower() == "resources.assets":
        return path.parent.parent
    if path.name.lower() == "spellcaster university_data":
        return path.parent
    if path.suffix.lower() == ".exe":
        return path.parent
    return path


def candidates() -> list[Path]:
    result = [
        Path.cwd(),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Spellcaster University"),
        Path(r"C:\Program Files\Steam\steamapps\common\Spellcaster University"),
        Path(r"D:\SteamLibrary\steamapps\common\Spellcaster University"),
    ]
    unique: list[Path] = []
    for item in result:
        item = normalize_game_root(item)
        if item not in unique:
            unique.append(item)
    return unique


def locate_game(explicit: str | None) -> Path:
    if explicit:
        root = normalize_game_root(explicit)
        if (root / ASSET_RELATIVE).is_file():
            return root
        raise FileNotFoundError(f"Не найден файл: {root / ASSET_RELATIVE}")

    for root in candidates():
        if (root / ASSET_RELATIVE).is_file():
            return root

    while True:
        print("\nУкажите папку, в которой находится Spellcaster University.exe")
        value = input("Путь: ").strip()
        root = normalize_game_root(value)
        if (root / ASSET_RELATIVE).is_file():
            return root
        print(f"Файл не найден: {root / ASSET_RELATIVE}")


def build_russian_asset(source_asset: Path, output_asset: Path) -> None:
    terms_payload = json.loads(
        bundled_path("data/i2_schema.json").read_text(encoding="utf-8")
    )
    translation_payload = json.loads(
        bundled_path("data/translations_ru.json").read_text(encoding="utf-8")
    )
    terms = terms_payload["terms"]
    translations = translation_payload["translations"]
    if len(terms) != 5208 or len(translations) != len(terms):
        raise RuntimeError("Повреждены данные перевода")
    if any(value is None for value in translations):
        raise RuntimeError("Перевод содержит незаполненные значения")

    env = UnityPy.load(str(source_asset))
    obj = next(item for item in env.objects if item.path_id == terms_payload["path_id"])
    original = obj.get_raw_data()
    rebuilt = bytearray(original[:56])
    write_i32(rebuilt, len(terms))

    for term, russian in zip(terms, translations):
        languages = [russian] * 9
        if term["term"] == "LANGUE_eng":
            languages = ["Русский"] * 9
        write_string(rebuilt, term["term"])
        write_i32(rebuilt, int(term["type"]))
        write_i32(rebuilt, len(languages))
        for language in languages:
            write_string(rebuilt, language)
        write_byte_array(rebuilt, term["flags"])
        write_i32(rebuilt, len(term["touch"]))
        for value in term["touch"]:
            write_string(rebuilt, value)

    tail = bytearray(original[int(terms_payload["tail_offset"]) :])
    google_update_frequency_offset = 492
    current_frequency = struct.unpack_from(
        "<i", tail, google_update_frequency_offset
    )[0]
    if current_frequency not in (1, 3):
        raise RuntimeError("Неожиданная структура языкового ресурса")
    struct.pack_into("<i", tail, google_update_frequency_offset, 1)
    rebuilt.extend(tail)
    obj.set_raw_data(bytes(rebuilt))
    output_asset.write_bytes(env.file.save())
    # UnityPy keeps the source stream alive while the environment exists.
    # Release it before Windows atomically replaces resources.assets.
    del obj
    del env
    gc.collect()


def install(game_root: Path) -> None:
    if game_is_running():
        raise RuntimeError("Сначала полностью закройте Spellcaster University")

    asset = game_root / ASSET_RELATIVE
    backup = game_root / BACKUP_RELATIVE
    current_hash = sha256(asset)
    if current_hash == RUSSIAN_SHA256:
        print("Русификация уже установлена.")
        return

    source = asset
    if current_hash != ORIGINAL_SHA256:
        if backup.is_file() and sha256(backup) == ORIGINAL_SHA256:
            source = backup
        else:
            raise RuntimeError(
                "Версия resources.assets не поддерживается. "
                "Проверьте целостность файлов игры в Steam и повторите установку."
            )

    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        print("Создание резервной копии...")
        shutil.copy2(source, backup)

    temporary = asset.with_name("resources.assets.russian.tmp")
    if temporary.exists():
        temporary.unlink()
    print("Сборка русского языкового ресурса. Это может занять несколько минут...")
    build_russian_asset(source, temporary)
    actual_hash = sha256(temporary)
    if actual_hash != RUSSIAN_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"Контрольная сумма результата не совпала: {actual_hash}"
        )
    os.replace(temporary, asset)
    print("\nРусификация успешно установлена.")


def restore(game_root: Path) -> None:
    if game_is_running():
        raise RuntimeError("Сначала полностью закройте Spellcaster University")
    asset = game_root / ASSET_RELATIVE
    backup = game_root / BACKUP_RELATIVE
    if not backup.is_file() or sha256(backup) != ORIGINAL_SHA256:
        raise RuntimeError("Корректная резервная копия не найдена")
    temporary = asset.with_name("resources.assets.restore.tmp")
    shutil.copy2(backup, temporary)
    os.replace(temporary, asset)
    print("Оригинальный файл игры восстановлен.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--game", help="Путь к папке игры")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true")
    group.add_argument("--restore", action="store_true")
    parser.add_argument("--no-pause", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(APP_NAME)
    print("Версия перевода: 1.0.0\n")
    try:
        root = locate_game(args.game)
        print(f"Игра: {root}")
        if args.restore:
            restore(root)
        elif args.install:
            install(root)
        else:
            print("\n1 — установить русификацию")
            print("2 — восстановить оригинал")
            choice = input("Выберите действие [1/2]: ").strip()
            if choice == "1":
                install(root)
            elif choice == "2":
                restore(root)
            else:
                print("Действие отменено.")
        return 0
    except Exception as error:
        print(f"\nОШИБКА: {error}")
        if os.environ.get("RU_INSTALLER_DEBUG") == "1":
            traceback.print_exc()
        return 1
    finally:
        if not args.no_pause:
            input("\nНажмите Enter, чтобы закрыть окно...")


if __name__ == "__main__":
    raise SystemExit(main())
