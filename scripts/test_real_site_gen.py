#!/usr/bin/env python3
"""Integration test: generate + deploy a real site via local LLM call.

Tests the full pipeline: generate_site() -> write_site_files() -> deploy_preview()
Verifies: no npm dependency, files written, deploy succeeds.
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/tmp/buildo-bot")

from bot.services.site_generator import generate_site  # noqa: E402
from bot.services import preview  # noqa: E402


async def main() -> None:
    test_prompt = (
        "Лендинг для маленькой кофейни «Зерно» в центре Москвы. "
        "Тёплый минимализм, секции: hero, меню (5 позиций), контакты, отзывы. "
        "Палитра: бежевый + тёмно-коричневый + кремовый. "
        "Шрифты: Playfair (заголовки) + Inter (тело). "
        "Контент на русском. Без плейсхолдеров."
    )
    print(f"Prompt: {test_prompt[:80]}...")

    print("\n[1/3] Generating site via LLM...")
    try:
        site = await generate_site(test_prompt)
    except Exception as e:
        print(f"  ✗ Generation failed: {e}")
        sys.exit(1)

    print(f"  ✓ Generated: {site.project_name}")
    print(f"    Framework: {site.framework}")
    print(f"    Files: {len(site.files)}")
    print(f"    Total size: {site.total_size_kb:.1f}KB")
    print(f"    Summary: {site.preview_summary[:100]}")

    if site.framework != "static-html":
        print(f"  ✗ Expected static-html, got {site.framework}")
        sys.exit(1)

    if not site.files or site.files[0].path != "index.html":
        print("  ✗ Expected single index.html file")
        sys.exit(1)

    print(f"\n[2/3] Deploying preview...")
    test_tg = 99999999
    test_site_id = str(uuid.uuid4())
    files_dicts = [{"path": f.path, "content": f.content} for f in site.files]
    try:
        result = await preview.deploy_preview(test_tg, test_site_id, files_dicts, site.project_name)
    except Exception as e:
        print(f"  ✗ Deploy failed: {e}")
        sys.exit(1)

    if not result.success:
        print(f"  ✗ Deploy returned failure: {result.error}")
        sys.exit(1)

    print(f"  ✓ Deployed: {result.url}")

    print(f"\n[3/3] Verifying files on disk...")
    site_path = Path.home() / "buildo-sites" / str(test_tg) / test_site_id
    public_path = Path.home() / "buildo-sites" / "public" / str(test_tg) / test_site_id
    if not (site_path / "index.html").exists():
        print(f"  ✗ Source index.html not found at {site_path}")
        sys.exit(1)
    if not (public_path / "index.html").exists():
        print(f"  ✗ Public index.html not found at {public_path}")
        sys.exit(1)
    src_size = (site_path / "index.html").stat().st_size
    pub_size = (public_path / "index.html").stat().st_size
    print(f"  ✓ Source: {site_path}/index.html ({src_size}b)")
    print(f"  ✓ Public: {public_path}/index.html ({pub_size}b)")

    print(f"\n  Preview snippet (first 400 chars):")
    print(f"  {((public_path / 'index.html').read_text()[:400])}...")

    print("\n  ✓ ALL CHECKS PASSED")

    # Cleanup
    import shutil
    shutil.rmtree(site_path, ignore_errors=True)
    shutil.rmtree(public_path, ignore_errors=True)
    print("  ✓ Cleanup done")


if __name__ == "__main__":
    asyncio.run(main())
