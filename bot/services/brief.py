"""Multi-step brief builder for site generation.

Юзер проходит 7-шаговый бриф через inline-кнопки:
1. Ниша (текст)
2. Стиль (vibrant/minimalist/editorial/brutalist + 🎲 случайный)
3. Секции (6 toggle)
4. Палитра (4 пресета)
5. CTA (5 шаблонов)
6. Hero-текст (текст)
7. Файл с ТЗ (опционально)
8. → Генерация
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# Стили сайтов
STYLES = {
    "vibrant": {
        "name": "⚡ Vibrant",
        "desc": "яркие акценты, градиенты, motion",
    },
    "minimalist": {
        "name": "🤍 Minimalist",
        "desc": "чистый whitespace, типографика, минимум цветов",
    },
    "editorial": {
        "name": "📰 Editorial",
        "desc": "как журнал, серифы, длинные тексты, фото сверху",
    },
    "brutalist": {
        "name": "🧱 Brutalist",
        "desc": "сырые формы, swiss grid, моноширинный, контраст",
    },
}

# Палитры
PALETTES = {
    "midnight_ocean": {
        "name": "🌊 Midnight Ocean",
        "colors": "#0A1628 / #06B6D4 / #FDFCF8 / #F59E0B",
        "desc": "тёмно-синий + cyan (наш бренд)",
    },
    "sunset_warm": {
        "name": "🌅 Sunset Warm",
        "colors": "#1A1A1A / #FF6B35 / #FFF8E7 / #E63946",
        "desc": "тёплый закат, оранжевый + коралл",
    },
    "nordic_cool": {
        "name": "❄️ Nordic Cool",
        "colors": "#F5F5F0 / #2D3142 / #EF8354 / #BFC0C0",
        "desc": "скандинавский, бежевый + серый",
    },
    "earth_tones": {
        "name": "🌿 Earth Tones",
        "colors": "#2C1810 / #D4A373 / #FAEDCD / #588157",
        "desc": "землистый, оливковый + песочный",
    },
}

# CTA шаблоны
CTA_TEMPLATES = {
    "book": "📅 Записаться",
    "buy": "💳 Купить",
    "contact": "✉️ Связаться",
    "subscribe": "🔔 Подписаться",
    "download": "📥 Скачать",
}

# Доступные секции
SECTIONS = {
    "hero": "🎯 Hero (главный экран)",
    "about": "📖 О нас",
    "services": "⚙️ Услуги",
    "portfolio": "🖼 Портфолио",
    "contacts": "📞 Контакты",
    "blog": "📝 Блог",
}


@dataclass
class BriefData:
    """Собранные данные брифа от юзера."""

    user_id: int
    niche: Optional[str] = None
    style: Optional[str] = None
    sections: list = field(default_factory=list)
    palette: Optional[str] = None
    cta: Optional[str] = None
    hero_text: Optional[str] = None
    extra_file_text: Optional[str] = None  # содержимое файла ТЗ
    extra_filename: Optional[str] = None

    def is_ready(self) -> bool:
        """Бриф готов когда все обязательные поля заполнены."""
        return all(
            [
                self.niche,
                self.style,
                self.sections,
                self.palette,
                self.cta,
                self.hero_text,
            ]
        )

    def missing_fields(self) -> list[str]:
        """Какие поля ещё не заполнены."""
        missing = []
        if not self.niche:
            missing.append("ниша")
        if not self.style:
            missing.append("стиль")
        if not self.sections:
            missing.append("секции")
        if not self.palette:
            missing.append("палитра")
        if not self.cta:
            missing.append("CTA")
        if not self.hero_text:
            missing.append("hero-текст")
        return missing

    def to_prompt(self) -> str:
        """Собрать полный промт для LLM из брифа."""
        style = STYLES.get(self.style or "", {})
        palette = PALETTES.get(self.palette or "", {})
        cta = CTA_TEMPLATES.get(self.cta or "", "📅 Записаться")
        sections_str = ", ".join(self.sections)

        prompt = f"""Ниша: {self.niche}

Стиль: {style.get('name', self.style)} ({style.get('desc', '')})

Секции: {sections_str}

Палитра: {palette.get('name', self.palette)} — {palette.get('colors', '')}
Описание палитры: {palette.get('desc', '')}

CTA (текст кнопки): {cta}

Hero-текст: {self.hero_text}
"""
        if self.extra_file_text:
            prompt += f"\nДополнительное ТЗ из файла «{self.extra_filename}»:\n{self.extra_file_text}\n"

        prompt += """
Сгенерируй современный, стильный сайт. Используй:
- Качественную типографику (Inter/Space Grotesk для body, Fraunces/Playfair для заголовков)
- Микро-анимации (hover effects, scroll animations, smooth transitions)
- Реальные фотографии через https://images.unsplash.com (URL.unsplash.com/photo-ID)
- Mobile-first responsive design
- Anti-slop: никаких emoji-цепочек, синих CTA, stock-photo-фраз
- Визуальная иерархия: чёткий hero, breathable whitespace, длинные тексты
- Движение: hover-эффекты, scroll animations, scroll-triggered reveals
- Плотность: достаточно контента чтобы сайт выглядел наполненным, не пустым
"""
        return prompt


# In-memory storage (в проде → Redis)
_briefs: dict[int, BriefData] = {}


def get_brief(user_id: int) -> BriefData:
    """Получить или создать бриф для юзера."""
    if user_id not in _briefs:
        _briefs[user_id] = BriefData(user_id=user_id)
    return _briefs[user_id]


def clear_brief(user_id: int) -> None:
    """Очистить бриф (после генерации или отмены)."""
    _briefs.pop(user_id, None)


# ============== enrich_prompt — rule-based парсер промта ==============


# Ключевые слова → стили
_STYLE_KEYWORDS = {
    "vibrant": [
        "ярк",
        "ярко",
        "цветн",
        "сочн",
        "пёстр",
        "весел",
        "агрессивн",
        "vibrant",
        "яркий",
        "яркая",
        "яркое",
        "кислот",
    ],
    "minimalist": [
        "минимал",
        "лаконич",
        "чист",
        "прост",
        "спокойн",
        "воздушн",
        "minimalist",
        "minimal",
        "чистый",
        "чистая",
    ],
    "editorial": [
        "журнал",
        "стать",
        "издани",
        "длинн",
        "много текст",
        "editorial",
        "magazine",
        "editorial-style",
        "лонгрид",
    ],
    "brutalist": [
        "брутал",
        "груб",
        "строг",
        "industrial",
        "loft",
        "хай-тек",
        "сыр",
        "бетон",
        "контрастн",
        "brutalist",
    ],
}

# Ключевые слова → палитры
_PALETTE_KEYWORDS = {
    "midnight_ocean": [
        "тёмн",
        "темн",
        "ночн",
        "moonlight",
        "midnight",
        "blue",
        "синий",
        "cyan",
        "голуб",
        "ocean",
        "океан",
        "глубок",
    ],
    "sunset_warm": [
        "тёпл",
        "тепл",
        "оранж",
        "закат",
        "sunset",
        "warm",
        "красн",
        "коралл",
        "оранжев",
    ],
    "nordic_cool": [
        "скандинав",
        "nordic",
        "северн",
        "холодн",
        "cool",
        "серо-бел",
        "бежев",
        "светл-сер",
    ],
    "earth_tones": [
        "земл",
        "природ",
        "earth",
        "оливк",
        "песочн",
        "коричнев",
        "беж",
        "натуральн",
        "эко",
    ],
}

# Секции — что нужно
_SECTION_KEYWORDS = {
    "hero": ["hero", "главн", "первый экран", "обложк"],
    "about": ["о нас", "о себе", "обо мне", "о студии", "о компан", "about"],
    "services": [
        "услуг",
        "service",
        "сервис",
        "предложен",
        "что мы дела",
        "продукт",
        "тариф",
        "цены",
        "пакет",
    ],
    "portfolio": [
        "портфолио",
        "portfolio",
        "кейс",
        "работ",
        "проект",
        "gallery",
        "галерея",
        "пример",
    ],
    "contacts": [
        "контакт",
        "связ",
        "адрес",
        "телефон",
        "обратн",
        "форма",
        "contact",
        "обратная связь",
        "напишите",
    ],
    "blog": ["блог", "стать", "новост", "blog", "article"],
    "menu": ["меню", "menu", "блюд", "кафе", "ресторан", "кофейн", "кухн"],
    "team": ["команд", "сотрудник", "team", "о нас с фот", "наша команд"],
    "testimonials": ["отзыв", "review", "testimonial", "рекомендац"],
}

# CTA — кнопки
_CTA_KEYWORDS = {
    "book": ["записаться", "запись", "бронир", "book", "appointment"],
    "buy": ["купить", "заказать", "оформить", "buy", "order", "корзин"],
    "contact": ["связаться", "написать", "позвонить", "contact", "обратн"],
    "subscribe": ["подписаться", "подписка", "subscribe", "newsletter"],
    "download": ["скачать", "download", "загрузить", "pdf", "презентац"],
    "menu": ["меню", "menu", "блюда", "смотреть меню"],
}

_DEFAULT_SECTIONS = ["hero", "about", "services", "contacts"]


def enrich_prompt(prompt: str) -> dict:
    """Парсит свободный промт юзера в структуру для LLM.

    Returns:
        dict with keys:
          - niche: str
          - style, style_name
          - palette, palette_name
          - sections: list[str]
          - cta, cta_label
          - to_prompt: callable — собирает полный промт
    """
    p_lower = prompt.lower()

    # Стиль — кто первый совпал
    style = "minimalist"
    style_score = 0
    for st, keywords in _STYLE_KEYWORDS.items():
        for kw in keywords:
            if kw in p_lower:
                if len(kw) > style_score:
                    style = st
                    style_score = len(kw)
                break

    # Палитра
    palette = "midnight_ocean"
    palette_score = 0
    for pl, keywords in _PALETTE_KEYWORDS.items():
        for kw in keywords:
            if kw in p_lower:
                if len(kw) > palette_score:
                    palette = pl
                    palette_score = len(kw)
                break

    # Секции — все совпавшие
    sections: list[str] = ["hero"]  # hero всегда
    for sec, keywords in _SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in p_lower:
                if sec not in sections:
                    sections.append(sec)
                break
    if sections == ["hero"]:
        # ничего не нашли — дефолт
        sections = _DEFAULT_SECTIONS.copy()

    # CTA
    cta = "contact"
    cta_score = 0
    for ct, keywords in _CTA_KEYWORDS.items():
        for kw in keywords:
            if kw in p_lower:
                if len(kw) > cta_score:
                    cta = ct
                    cta_score = len(kw)
                break

    # Ниша — берём первую фразу (до запятой) или первые 5 слов
    niche_match = re.split(r"[,.]", prompt, maxsplit=1)
    niche = niche_match[0].strip() if niche_match else prompt.strip()
    if len(niche) > 60:
        niche = " ".join(niche.split()[:5])

    # Hero-текст — генерим дефолт если не указан
    hero_line = f"{niche.title()} | Качественно, быстро, удобно"

    style_info = STYLES[style]
    palette_info = PALETTES[palette]
    cta_label = CTA_TEMPLATES[cta]

    def to_prompt() -> str:
        return f"""Ниша: {prompt}

Стиль: {style_info['name']} ({style_info['desc']})

Секции: {', '.join(sections)}

Палитра: {palette_info['name']} — {palette_info['colors']}
Описание палитры: {palette_info['desc']}

CTA (текст кнопки): {cta_label}

Hero-текст: {hero_line}

Сгенерируй современный, стильный сайт. Используй:
- Качественную типографику (Inter/Space Grotesk для body, Fraunces/Playfair для заголовков)
- Микро-анимации (hover effects, scroll animations, smooth transitions)
- Реальные фотографии через https://images.unsplash.com (URL.unsplash.com/photo-ID)
- Mobile-first responsive design
- Anti-slop: никаких emoji-цепочек, синих CTA, stock-photo-фраз
- Визуальная иерархия: чёткий hero, breathable whitespace, длинные тексты
- Движение: hover-эффекты, scroll animations, scroll-triggered reveals
- Плотность: достаточно контента чтобы сайт выглядел наполненным, не пустым
"""

    return {
        "niche": niche,
        "style": style,
        "style_name": style_info["name"],
        "palette": palette,
        "palette_name": palette_info["name"],
        "sections": sections,
        "cta": cta,
        "cta_label": cta_label,
        "to_prompt": to_prompt,
        "hero_line": hero_line,
    }
