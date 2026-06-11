#!/usr/bin/env python3
"""Generate Buildo bot scene images in unified style via OpenRouter/gemini-3.1-flash-image-preview.

Style guide (in prompt): midnight ocean + cyan tide + amber + paper cream,
flat geometric, dev-tool vibe, no text in images (we add via Telegram),
no people faces, abstract symbols (gears, code, hammers, blocks).
"""
import asyncio
import base64
import os
import sys
from pathlib import Path

import httpx

# Buildo brand style (per /root/12-brand-book.md)
STYLE = (
    "Flat geometric illustration, deep midnight ocean #0A1628 background, "
    "cyan tide #06B6D4 accents, warm amber #F59E0B highlights, paper cream #FDFCF8 surfaces. "
    "Bold geometric shapes, sharp lines, no gradients (or minimal 2-stop gradients), "
    "no text or letters, no human faces, abstract dev-tool symbols. "
    "Premium, modern, calm. Inspired by Linear/Vercel/Stripe aesthetic. "
    "Square aspect ratio."
)

# (filename, scene description, aspect)
SCENES = [
    ("welcome.png", "A friendly abstract composition: a glowing cyan hammer striking a stylized document/webpage block, "
                    "with floating geometric shapes around (circles, triangles, code brackets). "
                    "Represents 'building websites with AI'. Hero scene.", "1:1"),
    ("no_sites.png", "An empty cardboard box or shelf with a single amber light inside, surrounded by floating cyan dots. "
                     "Represents 'no sites yet, ready to be created'. Quiet, inviting.", "1:1"),
    ("generating.png", "Abstract cyan gears meshing with amber sparks, code brackets flying around, "
                       "a glowing webpage block being assembled in the center. "
                       "Represents 'AI generating your site'. Dynamic, in motion.", "1:1"),
    ("editing.png", "A cyan magnifying glass examining a document, amber pencil/mark hover above, "
                    "diff lines (+/-) glowing. Represents 'editing your site in dialog'.", "1:1"),
    ("published.png", "A stylized rocket made of cyan+amber geometric blocks launching from a paper-cream base, "
                      "leaving a cyan trail. Represents 'site published successfully'.", "1:1"),
    ("menu.png", "Three floating cards/panels (like a UI dashboard) on a midnight background, "
                 "with cyan connection lines between them. Represents 'main menu with all your sites'.", "1:1"),
    ("error.png", "A broken geometric puzzle piece falling apart, amber warning triangle in corner, "
                  "calm cyan tones. Represents 'something went wrong, but it's ok'.", "1:1"),
    ("referral.png", "Three connected nodes in cyan, with a central amber star/coin, "
                     "lines of light between them forming a triangle. Represents '3-level referral program'.", "1:1"),
    ("payment.png", "A stylized credit card with cyan chip, amber checkmark, and floating coins, "
                    "on a calm midnight background. Represents 'secure payment'.", "1:1"),
    ("admin.png", "A control panel mockup: sliders, switches, gauges, with cyan+amber indicators, "
                  "no text. Represents 'admin controls'.", "1:1"),
]


async def generate_one(client: httpx.AsyncClient, api_key: str, filename: str, desc: str, aspect: str) -> bool:
    prompt = f"{STYLE}\n\nScene: {desc}\n\nAspect ratio: {aspect}."
    print(f"Generating {filename}...")
    try:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "google/gemini-3.1-flash-image-preview",
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
            },
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        # Extract image from response — OR returns it in message.images[0].image_url.url as data URL
        msg = data["choices"][0]["message"]
        if "images" in msg and msg["images"]:
            img_url = msg["images"][0]["image_url"]["url"]
            if img_url.startswith("data:image"):
                b64 = img_url.split(",", 1)[1]
                Path(f"/tmp/buildo-bot/assets/bot/{filename}").write_bytes(base64.b64decode(b64))
                size = Path(f"/tmp/buildo-bot/assets/bot/{filename}").stat().st_size
                print(f"  ✓ {filename} ({size//1024}KB)")
                return True
        # Fallback: check content for image
        print(f"  ✗ {filename} — no image in response: {data}")
        return False
    except Exception as e:
        print(f"  ✗ {filename} — {e}")
        return False


async def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        # Try from env file
        env_file = Path.home() / ".buildo-bot.env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY=") or line.startswith("OR_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not api_key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    Path("/tmp/buildo-bot/assets/bot").mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient() as client:
        results = []
        for fn, desc, asp in SCENES:
            ok = await generate_one(client, api_key, fn, desc, asp)
            results.append(ok)
            await asyncio.sleep(2)  # rate-limit courtesy

    success = sum(results)
    print(f"\n{success}/{len(SCENES)} images generated")
    sys.exit(0 if success == len(SCENES) else 1)


if __name__ == "__main__":
    asyncio.run(main())
