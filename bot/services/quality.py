"""Оценка качества сгенерированного сайта по 7 критериям.

Критерии (по taste-skill v2 + impeccable):
1. visual_hierarchy — чёткая иерархия заголовков, отступов
2. typography — качественные шрифты, размеры, line-height
3. color — гармоничная палитра, контраст
4. density — достаточно контента, не пустой
5. motion — микро-анимации, hover-эффекты
6. originality — не шаблонный, есть характер
7. anti_slop — нет AI-isms (эмодзи-цепочек, generic фраз)

Score: 0-10. Если < 6.5 — рекомендуем перегенерацию.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class QualityScore:
    """Оценка качества сайта."""

    visual_hierarchy: float
    typography: float
    color: float
    density: float
    motion: float
    originality: float
    anti_slop: float
    overall: float
    improvements: list[str]

    def to_dict(self) -> dict:
        return {
            "visual_hierarchy": self.visual_hierarchy,
            "typography": self.typography,
            "color": self.color,
            "density": self.density,
            "motion": self.motion,
            "originality": self.originality,
            "anti_slop": self.anti_slop,
            "overall": self.overall,
            "improvements": self.improvements,
        }


# AI-slop слова и фразы (anti-slop detector)
SLOP_WORDS = [
    "unleash",
    "elevate",
    "revolutionize",
    "game-changer",
    "cutting-edge",
    "leverage",
    "synergy",
    "paradigm",
    "disrupt",
    "next-generation",
    "🚀",
    "💫",
    "✨",
    "🎯",
    "💎",
    "🔥",  # AI любит эти эмодзи
]


# Generics в hero-секциях (часто генерируются LLM)
GENERIC_HERO_PHRASES = [
    "welcome to",
    "welcome our",
    "your one-stop",
    "all you need",
    "we are passionate",
    "we are dedicated",
    "committed to excellence",
    "innovative solutions",
    "transform your",
    "empowering",
]


def score_site(html: str) -> QualityScore:
    """Оценить качество HTML/CSS/JS сайта.

    Args:
        html: полный HTML код сайта

    Returns:
        QualityScore с оценками по 7 критериям + список улучшений
    """
    improvements: list[str] = []

    visual_hierarchy = _score_visual_hierarchy(html, improvements)
    typography = _score_typography(html, improvements)
    color = _score_color(html, improvements)
    density = _score_density(html, improvements)
    motion = _score_motion(html, improvements)
    originality = _score_originality(html, improvements)
    anti_slop = _score_anti_slop(html, improvements)

    # Среднее
    overall = (
        visual_hierarchy
        + typography
        + color
        + density
        + motion
        + originality
        + anti_slop
    ) / 7.0

    return QualityScore(
        visual_hierarchy=visual_hierarchy,
        typography=typography,
        color=color,
        density=density,
        motion=motion,
        originality=originality,
        anti_slop=anti_slop,
        overall=round(overall, 1),
        improvements=improvements,
    )


def _score_visual_hierarchy(html: str, improvements: list[str]) -> float:
    """Оценка визуальной иерархии."""
    score = 5.0

    # Чёткие заголовки h1, h2, h3
    h1_count = len(re.findall(r"<h1", html, re.IGNORECASE))
    h2_count = len(re.findall(r"<h2", html, re.IGNORECASE))

    if h1_count >= 1 and h2_count >= 2:
        score += 1.5
    elif h1_count >= 1:
        score += 0.5
    else:
        improvements.append("добавь чёткие h1/h2 заголовки")

    # Семантические секции
    sections = len(re.findall(r"<section", html, re.IGNORECASE))
    if sections >= 4:
        score += 1.5
    elif sections >= 2:
        score += 0.5
    else:
        improvements.append("раздели контент на 4+ секции через <section>")

    # Whitespace в стилях (padding/margin)
    padding_count = len(re.findall(r"padding\s*:", html, re.IGNORECASE))
    margin_count = len(re.findall(r"margin\s*:", html, re.IGNORECASE))
    if padding_count + margin_count >= 10:
        score += 1.5
    elif padding_count + margin_count >= 5:
        score += 0.5
    else:
        improvements.append("добавь больше padding/margin для breathable whitespace")

    return min(score, 10.0)


def _score_typography(html: str, improvements: list[str]) -> float:
    """Оценка типографики."""
    score = 5.0

    # Качественные шрифты
    has_inter = "Inter" in html
    has_space_grotesk = "Space Grotesk" in html
    has_fraunces = "Fraunces" in html
    has_playfair = "Playfair" in html

    if has_fraunces or has_playfair:
        score += 2.0  # serif для заголовков — premium
    elif has_inter or has_space_grotesk:
        score += 1.0
    else:
        improvements.append(
            "используй Inter/Space Grotesk (sans) + Fraunces/Playfair (serif)"
        )

    # line-height
    line_heights = len(re.findall(r"line-height\s*:", html, re.IGNORECASE))
    if line_heights >= 3:
        score += 1.5
    elif line_heights >= 1:
        score += 0.5

    # font-size вариации
    font_sizes = len(re.findall(r"font-size\s*:", html, re.IGNORECASE))
    if font_sizes >= 5:
        score += 1.5
    elif font_sizes >= 2:
        score += 0.5
    else:
        improvements.append("добавь вариации font-size (5+ разных размеров)")

    return min(score, 10.0)


def _score_color(html: str, improvements: list[str]) -> float:
    """Оценка цветовой палитры."""
    score = 5.0

    # Количество уникальных цветов
    hex_colors = set(re.findall(r"#[0-9A-Fa-f]{3,6}", html))
    rgb_colors = set(re.findall(r"rgb\([^)]+\)", html))

    total_colors = len(hex_colors) + len(rgb_colors)

    if 4 <= total_colors <= 8:
        score += 3.0  # идеальная палитра
    elif 2 <= total_colors <= 12:
        score += 1.5
    else:
        if total_colors < 2:
            improvements.append("добавь цветовые акценты (используй CSS variables)")
        else:
            improvements.append("сократи палитру до 4-8 цветов")

    # CSS variables (признак продуманной системы)
    css_vars = len(re.findall(r"--[a-z-]+\s*:", html, re.IGNORECASE))
    if css_vars >= 3:
        score += 2.0
    elif css_vars >= 1:
        score += 0.5

    return min(score, 10.0)


def _score_density(html: str, improvements: list[str]) -> float:
    """Оценка плотности контента."""
    score = 5.0

    # Длина HTML
    html_len = len(html)

    if html_len >= 15_000:
        score += 3.0  # плотный, наполненный
    elif html_len >= 8_000:
        score += 2.0
    elif html_len >= 4_000:
        score += 1.0
    else:
        improvements.append("добавь больше контента (сейчас сайт слишком пустой)")

    # Количество слов в body
    text_content = re.sub(r"<[^>]+>", " ", html)
    words = len(text_content.split())
    if words >= 200:
        score += 2.0
    elif words >= 100:
        score += 1.0
    elif words < 50:
        improvements.append("добавь больше осмысленного текста (200+ слов)")

    return min(score, 10.0)


def _score_motion(html: str, improvements: list[str]) -> float:
    """Оценка motion / анимаций."""
    score = 3.0

    # transition / animation
    transitions = len(re.findall(r"transition\s*:", html, re.IGNORECASE))
    animations = len(re.findall(r"@keyframes|animation\s*:", html, re.IGNORECASE))

    if transitions >= 5 and animations >= 1:
        score += 4.0
    elif transitions >= 3:
        score += 2.5
    elif transitions >= 1:
        score += 1.0
    else:
        improvements.append("добавь transition на hover-эффекты")

    # :hover стили
    hovers = len(re.findall(r":hover", html, re.IGNORECASE))
    if hovers >= 5:
        score += 2.0
    elif hovers >= 2:
        score += 1.0
    else:
        improvements.append("добавь :hover эффекты на интерактивные элементы")

    # transform
    transforms = len(re.findall(r"transform\s*:", html, re.IGNORECASE))
    if transforms >= 2:
        score += 1.0

    return min(score, 10.0)


def _score_originality(html: str, improvements: list[str]) -> float:
    """Оценка оригинальности."""
    score = 5.0

    # Проверка на template-ность
    if "Lorem ipsum" in html:
        score -= 3.0
        improvements.append("убери Lorem ipsum — используй реальный контент")

    # Gradient (признак modern design)
    gradients = len(re.findall(r"gradient", html, re.IGNORECASE))
    if gradients >= 1:
        score += 1.0

    # Custom shapes / SVG
    svgs = len(re.findall(r"<svg", html, re.IGNORECASE))
    if svgs >= 1:
        score += 1.0

    # Glassmorphism / blur
    blurs = len(re.findall(r"backdrop-filter|blur\(", html, re.IGNORECASE))
    if blurs >= 1:
        score += 1.0

    # Кастомные CSS patterns
    grids = len(re.findall(r"display\s*:\s*grid", html, re.IGNORECASE))
    if grids >= 1:
        score += 1.0

    return max(min(score, 10.0), 0.0)


def _score_anti_slop(html: str, improvements: list[str]) -> float:
    """Anti-slop: детекция AI-isms."""
    score = 10.0

    html_lower = html.lower()

    # Slop-слова
    for word in SLOP_WORDS:
        if word.lower() in html_lower:
            score -= 0.5
            if "AI-isms" not in str(improvements):
                improvements.append(f"убери AI-cliché: «{word}»")

    # Generic hero фразы
    for phrase in GENERIC_HERO_PHRASES:
        if phrase in html_lower:
            score -= 1.0
            improvements.append(f"замени generic фразу: «{phrase}»")

    return max(score, 0.0)


def format_score_for_user(score: QualityScore) -> str:
    """Отформатировать оценку для показа юзеру."""
    lines = [
        f"🎨 Оценка качества: <b>{score.overall}/10</b>\n",
        f"├ Визуальная иерархия: {score.visual_hierarchy}/10",
        f"├ Типографика: {score.typography}/10",
        f"├ Цветовая палитра: {score.color}/10",
        f"├ Плотность: {score.density}/10",
        f"├ Движение: {score.motion}/10",
        f"├ Оригинальность: {score.originality}/10",
        f"└ Anti-slop: {score.anti_slop}/10",
    ]

    if score.improvements:
        lines.append("\n💡 Можно улучшить:")
        for imp in score.improvements[:3]:  # топ-3
            lines.append(f"   • {imp}")

    return "\n".join(lines)
