#!/usr/bin/env python3
"""Acquire real Spirited Away character source images from public wiki media."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image, UnidentifiedImageError


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = WORKSPACE_ROOT / "manifests"
DEFAULT_SOURCE_MANIFEST = "configs/characters/spirited_away_sources.internal_rnd.json"
DEFAULT_SOURCE_ROOT = "data/character_refs_sources"
DEFAULT_PREVIOUS_MANIFEST = "manifests/character_real_sources_acquisition.latest.json"
DEFAULT_MANIFEST_PREFIX = "character_real_sources_acquisition"
DEFAULT_CATEGORY_TITLE = "Category:Spirited_Away_images"
DEFAULT_SITE = "https://ghibli.fandom.com"
DEFAULT_API = f"{DEFAULT_SITE}/api.php"
USER_AGENT = "spirited-away-internal-rnd-acquirer/1.0"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
SKIP_EXTENSIONS = {".gif", ".svg"}


@dataclass(frozen=True)
class Character:
    character_id: str
    display_name: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_token(raw: str) -> str:
    chars = [c.lower() if c.isalnum() else "_" for c in raw.strip()]
    normalized = "".join(chars).strip("_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def _to_repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mime_to_extension(mime: str, url: str) -> str:
    mime_lower = mime.strip().lower()
    if "jpeg" in mime_lower or "jpg" in mime_lower:
        return ".jpg"
    if "png" in mime_lower:
        return ".png"
    if "webp" in mime_lower:
        return ".webp"
    if "bmp" in mime_lower:
        return ".bmp"
    if "tiff" in mime_lower:
        return ".tiff"

    suffix = Path(url.split("?", maxsplit=1)[0]).suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS | SKIP_EXTENSIONS:
        return suffix
    return ".jpg"


def _boundary_match(haystack_tokenized: str, needle: str) -> bool:
    candidate = _normalize_token(needle)
    if not candidate:
        return False
    return bool(re.search(rf"(^|_){re.escape(candidate)}(_|$)", haystack_tokenized))


def _quality_hint_score(title: str) -> int:
    tokenized = _normalize_token(title)
    score = 0
    if "4k" in tokenized:
        score += 4
    if "hd" in tokenized:
        score += 3
    if "promo" in tokenized:
        score += 3
    if "storyboard" in tokenized or "sketch" in tokenized:
        score -= 5
    if "gif" in tokenized:
        score -= 8
    return score


def _manual_keyword_overrides() -> dict[str, list[str]]:
    return {
        "chihiro_ogino": ["chihiro", "sen"],
        "yubaba": ["yubaba"],
        "haku": ["haku", "nigihayami", "kohakunushi", "kohaku"],
        "no_face": ["no_face", "no-face", "no face", "noface", "kaonashi"],
        "kamaji": ["kamaji", "kamajii"],
        "lin": ["lin", "rin"],
        "zeniba": ["zeniba", "zeniiba"],
        "boh": ["boh", "bou", "baby"],
        "akio_ogino": ["akio", "father", "dad"],
        "yuko_ogino": ["yuko", "yuuko", "mother", "mom", "parents", "parent"],
        "stink_spirit": [
            "stink_spirit",
            "stink spirit",
            "stink_god",
            "stink god",
            "river_spirit",
            "river spirit",
            "kawa_no_kami",
            "kawa no kami",
            "river_sprit",
        ],
        "river_spirit": ["river_spirit", "river spirit", "kawa_no_kami", "kawa no kami", "river_sprit"],
        "aogaeru": ["aogaeru", "frog"],
    }


def _manual_preferred_titles() -> dict[str, list[str]]:
    return {
        "stink_spirit": [
            "File:River Spirit in tub.png",
            "File:River spirit leaving bathhouse.png",
            "File:River spirit leaving the bathhouse.png",
            "File:River spirit free fly in air.png",
            "File:River spirit lifting Chihiro.jpg",
            "File:River spirit over bridge.png",
            "File:Chihiro and river spirit.png",
            "File:Chihiro, Yubaba, and river sprit.png",
            "File:RiverSpiritFilth.png",
            "File:RiverSpiritFullForm.png",
            "File:Spirited_away_08.png",
            "File:River Spirited face after wash.png",
        ],
        "yuko_ogino": [
            "File:Chihiro and parents in car.jpg",
            "File:Chihiro's parents eat like pig.jpg",
            "File:Chihiro sees her parents turning into pigs.png",
            "File:Chihiro sees parents transformed the pig.png",
            "File:Chihiro in car.jpg",
            "File:Chihiro in the backseat.png",
            "File:Chihiro in car with flowers.jpg",
            "File:Akio Ogino, Ichihiro Ogino, and Chihiro.jpg",
        ],
    }


def _load_characters(source_manifest_path: Path) -> list[Character]:
    payload = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    rows = payload.get("characters", [])
    if not isinstance(rows, list):
        raise ValueError("manifest field 'characters' must be a list")

    manual = _manual_keyword_overrides()
    characters: list[Character] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        character_id = _normalize_token(str(row.get("character_id", "")))
        if not character_id:
            continue

        display_name = str(row.get("display_name", character_id)).strip() or character_id
        aliases_raw = row.get("aliases", [])
        aliases = tuple(a for a in aliases_raw if isinstance(a, str) and a.strip())

        keyword_set: set[str] = {
            character_id,
            display_name,
            display_name.replace("-", " "),
            character_id.replace("_", " "),
        }
        keyword_set.update(aliases)
        keyword_set.update(manual.get(character_id, []))

        keywords = tuple(sorted({_normalize_token(k) for k in keyword_set if _normalize_token(k)}))
        characters.append(
            Character(
                character_id=character_id,
                display_name=display_name,
                aliases=aliases,
                keywords=keywords,
            )
        )

    return sorted(characters, key=lambda row: row.character_id)


def _purge_generated_files(source_root: Path, characters: list[Character]) -> list[str]:
    removed: list[str] = []
    for character in characters:
        folder = source_root / character.character_id
        if not folder.exists() or not folder.is_dir():
            continue
        for candidate in folder.glob("*local_refs__gen*"):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            candidate.unlink(missing_ok=True)
            removed.append(_to_repo_relative(candidate))
    return sorted(removed)


def _index_existing_files(source_root: Path, characters: list[Character]) -> tuple[dict[str, Path], dict[str, set[str]]]:
    hash_to_path: dict[str, Path] = {}
    by_character: dict[str, set[str]] = {character.character_id: set() for character in characters}

    for character in characters:
        folder = source_root / character.character_id
        folder.mkdir(parents=True, exist_ok=True)
        for candidate in sorted(folder.glob("*"), key=lambda path: path.as_posix().lower()):
            if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            sha = _sha256_file(candidate)
            hash_to_path[sha] = candidate
            by_character[character.character_id].add(sha)

    return hash_to_path, by_character


def _load_previous_source_url_cache(previous_manifest_path: Path) -> dict[str, dict[str, str]]:
    if not previous_manifest_path.exists():
        return {}

    try:
        payload = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    cache: dict[str, dict[str, str]] = {}
    for row in payload.get("images", []):
        if not isinstance(row, dict):
            continue
        source_url = str(row.get("source_url", "")).strip()
        sha256 = str(row.get("sha256", "")).strip().lower()
        path = str(row.get("local_path", "")).strip()
        status = str(row.get("download_status", "")).strip().lower()
        if not source_url or not sha256 or not path:
            continue
        if status not in {"downloaded", "reused_existing"}:
            continue
        cache[source_url] = {"sha256": sha256, "path": path}
    return cache


def _fetch_category_file_titles(session: requests.Session, api_url: str, category_title: str) -> list[str]:
    params: dict[str, Any] = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category_title,
        "cmnamespace": "6",
        "cmlimit": "500",
        "format": "json",
    }
    titles: list[str] = []

    while True:
        response = session.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        members = payload.get("query", {}).get("categorymembers", [])
        titles.extend([str(row.get("title", "")) for row in members if isinstance(row, dict)])

        next_token = payload.get("continue")
        if not isinstance(next_token, dict):
            break
        params.update(next_token)

    return sorted({title for title in titles if title.startswith("File:")})


def _search_file_titles(session: requests.Session, api_url: str, query: str, limit: int = 50) -> list[str]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",
        "srlimit": str(limit),
        "format": "json",
    }
    response = session.get(api_url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    results = payload.get("query", {}).get("search", [])
    return [str(row.get("title", "")) for row in results if isinstance(row, dict) and str(row.get("title", "")).startswith("File:")]


def _fetch_image_info(session: requests.Session, api_url: str, title: str) -> dict[str, Any] | None:
    params = {
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|timestamp",
        "format": "json",
    }
    response = session.get(api_url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    pages = payload.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return None

    page = next(iter(pages.values()), None)
    if not isinstance(page, dict):
        return None
    imageinfo_rows = page.get("imageinfo", [])
    if not isinstance(imageinfo_rows, list) or not imageinfo_rows:
        return None

    info = imageinfo_rows[0]
    if not isinstance(info, dict):
        return None
    url = str(info.get("url", "")).strip()
    if not url:
        return None

    return {
        "title": title,
        "source_url": url,
        "page_url": str(info.get("descriptionurl", "")).strip(),
        "width": int(info.get("width", 0) or 0),
        "height": int(info.get("height", 0) or 0),
        "mime": str(info.get("mime", "")).strip(),
        "source_timestamp": str(info.get("timestamp", "")).strip(),
    }


def _candidate_score(character: Character, file_title: str) -> int:
    name = file_title.replace("File:", "", 1)
    stem = Path(name).stem
    tokenized = _normalize_token(stem)
    score = 0
    match_count = 0
    for keyword in character.keywords:
        if _boundary_match(tokenized, keyword):
            match_count += 1
            score += max(2, len(keyword) // 3)
    if match_count == 0:
        return 0
    score += _quality_hint_score(stem)
    return score


def _build_character_candidates(
    characters: list[Character],
    base_titles: list[str],
    search_titles: dict[str, list[str]],
) -> dict[str, list[str]]:
    preferred_titles = _manual_preferred_titles()
    candidate_map: dict[str, list[tuple[int, str]]] = {character.character_id: [] for character in characters}

    all_titles = sorted(set(base_titles + [title for rows in search_titles.values() for title in rows]))
    all_titles_set = set(all_titles)

    for character in characters:
        preferred = preferred_titles.get(character.character_id, [])
        for index, title in enumerate(preferred):
            if title not in all_titles_set:
                continue
            candidate_map[character.character_id].append((10_000 - index, title))

    for title in all_titles:
        for character in characters:
            score = _candidate_score(character, title)
            if score > 0:
                candidate_map[character.character_id].append((score, title))

    ordered: dict[str, list[str]] = {}
    for character in characters:
        ranked = sorted(
            candidate_map[character.character_id],
            key=lambda row: (-row[0], row[1].lower()),
        )
        unique_titles: list[str] = []
        seen: set[str] = set()
        for _, title in ranked:
            if title in seen:
                continue
            seen.add(title)
            unique_titles.append(title)
        ordered[character.character_id] = unique_titles
    return ordered


def _download_to_temp(session: requests.Session, url: str, timeout: int) -> Path:
    response = session.get(url, stream=True, timeout=timeout)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".img") as tmp:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                tmp.write(chunk)
        return Path(tmp.name)


def _safe_open_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            return int(width), int(height)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"invalid image: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire real Spirited Away character source still images")
    parser.add_argument("--source-manifest", default=DEFAULT_SOURCE_MANIFEST, help="Character source manifest")
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT, help="Root directory for character source folders")
    parser.add_argument("--min-per-character", type=int, default=4, help="Minimum usable images per character")
    parser.add_argument("--max-per-character", type=int, default=6, help="Maximum kept images per character")
    parser.add_argument("--min-width", type=int, default=640, help="Minimum width")
    parser.add_argument("--min-height", type=int, default=360, help="Minimum height")
    parser.add_argument("--request-timeout", type=int, default=60, help="HTTP timeout in seconds")
    parser.add_argument("--category-title", default=DEFAULT_CATEGORY_TITLE, help="MediaWiki category title")
    parser.add_argument("--site", default=DEFAULT_SITE, help="Media site root URL")
    parser.add_argument("--api-url", default=DEFAULT_API, help="MediaWiki API URL")
    parser.add_argument("--manifest-prefix", default=DEFAULT_MANIFEST_PREFIX, help="Output manifest filename prefix")
    parser.add_argument("--previous-manifest", default=DEFAULT_PREVIOUS_MANIFEST, help="Previous manifest for URL cache")
    parser.add_argument("--purge-generated", action="store_true", help="Remove prior synthetic *local_refs__gen* files")
    args = parser.parse_args()

    if args.min_per_character < 1:
        raise ValueError("--min-per-character must be >= 1")
    if args.max_per_character < args.min_per_character:
        raise ValueError("--max-per-character must be >= --min-per-character")

    source_manifest_path = _resolve_path(args.source_manifest)
    source_root = _resolve_path(args.source_root)
    previous_manifest_path = _resolve_path(args.previous_manifest)
    source_root.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    characters = _load_characters(source_manifest_path)

    removed_generated = _purge_generated_files(source_root, characters) if args.purge_generated else []
    hash_to_existing_path, existing_by_character = _index_existing_files(source_root, characters)
    known_hashes: set[str] = set(hash_to_existing_path.keys())
    previous_source_url_cache = _load_previous_source_url_cache(previous_manifest_path)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    base_titles = _fetch_category_file_titles(session, args.api_url, args.category_title)

    search_titles_by_character: dict[str, list[str]] = {}
    for character in characters:
        search_queries = [
            f'"{character.display_name}" "Spirited Away"',
            f'"{character.character_id.replace("_", " ")}" "Spirited Away"',
        ]
        for alias in character.aliases[:3]:
            search_queries.append(f'"{alias}" "Spirited Away"')

        titles: list[str] = []
        for query in search_queries:
            try:
                titles.extend(_search_file_titles(session, args.api_url, query, limit=30))
            except requests.RequestException:
                continue
        search_titles_by_character[character.character_id] = sorted(set(titles))

    candidate_map = _build_character_candidates(characters, base_titles, search_titles_by_character)

    used_source_urls: set[str] = set()
    images_manifest: list[dict[str, Any]] = []
    per_character_summary: list[dict[str, Any]] = []

    process_order = sorted(characters, key=lambda row: (len(candidate_map.get(row.character_id, [])), row.character_id))

    for character in process_order:
        character_id = character.character_id
        folder = source_root / character_id
        folder.mkdir(parents=True, exist_ok=True)

        kept_hashes = set(existing_by_character.get(character_id, set()))
        downloaded_count = 0
        reused_count = 0
        failed_count = 0
        considered_count = 0

        for title in candidate_map.get(character_id, []):
            if len(kept_hashes) >= args.max_per_character:
                break

            considered_count += 1
            info_row: dict[str, Any] = {
                "character_id": character_id,
                "candidate_title": title,
                "source_url": "",
                "page_url": f"{args.site}/wiki/{title.replace(' ', '_')}",
                "download_status": "skipped",
                "width": None,
                "height": None,
                "sha256": "",
                "local_path": "",
                "timestamp": _now_iso(),
            }

            try:
                image_info = _fetch_image_info(session, args.api_url, title)
                if image_info is None:
                    info_row["download_status"] = "missing_imageinfo"
                    images_manifest.append(info_row)
                    failed_count += 1
                    continue

                source_url = str(image_info["source_url"])
                page_url = str(image_info["page_url"])
                width = int(image_info["width"])
                height = int(image_info["height"])
                mime = str(image_info["mime"])
                ext = _mime_to_extension(mime, source_url)

                info_row.update(
                    {
                        "source_url": source_url,
                        "page_url": page_url or info_row["page_url"],
                        "width": width,
                        "height": height,
                    }
                )

                if source_url in used_source_urls:
                    info_row["download_status"] = "duplicate_source_url"
                    images_manifest.append(info_row)
                    continue

                if ext in SKIP_EXTENSIONS:
                    info_row["download_status"] = "unsupported_extension"
                    images_manifest.append(info_row)
                    continue

                if width < args.min_width or height < args.min_height:
                    info_row["download_status"] = "low_resolution"
                    images_manifest.append(info_row)
                    continue

                cached = previous_source_url_cache.get(source_url)
                if cached:
                    cached_path = _resolve_path(cached["path"])
                    cached_sha = str(cached["sha256"]).lower()
                    if cached_path.exists() and cached_path.is_file() and _sha256_file(cached_path) == cached_sha:
                        if cached_sha in kept_hashes:
                            info_row.update(
                                {
                                    "download_status": "reused_existing",
                                    "sha256": cached_sha,
                                    "local_path": _to_repo_relative(cached_path),
                                }
                            )
                            images_manifest.append(info_row)
                            reused_count += 1
                            used_source_urls.add(source_url)
                            continue

                tmp_path = _download_to_temp(session, source_url, timeout=args.request_timeout)
                try:
                    sha = _sha256_file(tmp_path)
                    if sha in known_hashes or sha in kept_hashes:
                        info_row.update(
                            {
                                "download_status": "duplicate_hash",
                                "sha256": sha,
                            }
                        )
                        images_manifest.append(info_row)
                        continue

                    actual_width, actual_height = _safe_open_image(tmp_path)
                    if actual_width < args.min_width or actual_height < args.min_height:
                        info_row.update(
                            {
                                "download_status": "low_resolution",
                                "width": actual_width,
                                "height": actual_height,
                            }
                        )
                        images_manifest.append(info_row)
                        continue

                    if ext not in SUPPORTED_EXTENSIONS:
                        ext = ".jpg"

                    filename = f"{character_id}__real__{sha[:12]}{ext}"
                    dest_path = folder / filename
                    if not dest_path.exists():
                        dest_path.write_bytes(tmp_path.read_bytes())

                    kept_hashes.add(sha)
                    known_hashes.add(sha)
                    used_source_urls.add(source_url)
                    hash_to_existing_path[sha] = dest_path

                    info_row.update(
                        {
                            "download_status": "downloaded",
                            "width": actual_width,
                            "height": actual_height,
                            "sha256": sha,
                            "local_path": _to_repo_relative(dest_path),
                        }
                    )
                    images_manifest.append(info_row)
                    downloaded_count += 1
                finally:
                    tmp_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                info_row["download_status"] = "failed"
                info_row["error"] = str(exc)
                images_manifest.append(info_row)
                failed_count += 1

        gap = max(0, args.min_per_character - len(kept_hashes))
        per_character_summary.append(
            {
                "character_id": character_id,
                "display_name": character.display_name,
                "target_min": args.min_per_character,
                "target_max": args.max_per_character,
                "kept_count": len(kept_hashes),
                "downloaded_count": downloaded_count,
                "reused_count": reused_count,
                "failed_attempts": failed_count,
                "candidates_considered": considered_count,
                "gap_to_min": gap,
            }
        )

    per_character_summary = sorted(per_character_summary, key=lambda row: str(row["character_id"]))

    gaps = [row for row in per_character_summary if int(row["gap_to_min"]) > 0]

    payload = {
        "version": "1.0.0",
        "generated_at": _now_iso(),
        "valid": len(gaps) == 0,
        "source_manifest": _to_repo_relative(source_manifest_path),
        "source_root": _to_repo_relative(source_root),
        "site": args.site,
        "api_url": args.api_url,
        "category_title": args.category_title,
        "min_per_character": args.min_per_character,
        "max_per_character": args.max_per_character,
        "min_width": args.min_width,
        "min_height": args.min_height,
        "removed_generated_files": removed_generated,
        "summary": {
            "characters": len(per_character_summary),
            "characters_meeting_min": len(per_character_summary) - len(gaps),
            "characters_with_gaps": len(gaps),
            "images_downloaded": len([row for row in images_manifest if row.get("download_status") == "downloaded"]),
            "images_reused": len([row for row in images_manifest if row.get("download_status") == "reused_existing"]),
            "images_failed": len([row for row in images_manifest if row.get("download_status") == "failed"]),
        },
        "characters": per_character_summary,
        "images": images_manifest,
    }

    timestamp = _timestamp_utc()
    timestamped_manifest = MANIFESTS_DIR / f"{args.manifest_prefix}.{timestamp}.json"
    latest_manifest = MANIFESTS_DIR / f"{args.manifest_prefix}.latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    timestamped_manifest.write_text(text, encoding="utf-8")
    latest_manifest.write_text(text, encoding="utf-8")

    print(text)
    return 0 if not gaps else 1


if __name__ == "__main__":
    raise SystemExit(main())

