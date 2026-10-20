
"""Filter Avito listings by Chechen Republic address."""

import re
import unicodedata
from typing import Optional


REGION_MARKERS = (
    "чеченская республика",
    "чеченская респ",
    "республика чечня",
    "чечня",
)

# Названия населённых пунктов, достаточно характерные для Чечни.
CHECHNYA_LOCALITIES = (
    "грозный",
    "аргун",
    "гудермес",
    "шали",
    "урус мартан",
    "ачхой мартан",
    "наурская",
    "знаменское",
    "серноводское",
    "курчалой",
    "шелковская",
    "гойты",
    "гикало",
    "надтеречное",
    "бачиюрт",
    "итум кали",
    "ведено",
    "ножай юрт",
    "старые атаги",
    "алхан юрт",
    "ассиновская",
    "аллерой",
    "толстой юрт",
    "чири юрт",
    "сержень юрт",
    "белгатой",
    "цоци юрт",
    "мескер юрт",
    "катыр юрт",
    "самашки",
    "гехи",
    "гехи чу",
    "бамут",
    "шалажи",
    "кулары",
)

# Эти названия неоднозначны без явного указания региона.
AMBIGUOUS_LOCALITIES = (
    "петропавловская",
    "пригородное",
)

OTHER_REGION_MARKERS = (
    "москва",
    "московская область",
    "санкт петербург",
    "дагестан",
    "республика дагестан",
    "махачкала",
    "ингушетия",
    "республика ингушетия",
    "назрань",
    "ставропольский край",
    "ставрополь",
    "кабардино балкария",
    "нальчик",
    "северная осетия",
    "владикавказ",
    "ростовская область",
    "ростов на дону",
    "краснодарский край",
    "краснодар",
)


def _normalize(value: Optional[str]) -> str:
    """Нормализует адрес для сравнения."""
    if not value:
        return ""

    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("ё", "е")
    value = value.replace("—", " ").replace("–", " ")
    value = value.replace("-", " ")
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    """Ищет целую фразу, а не произвольную подстроку."""
    normalized_phrase = _normalize(phrase)
    if not text or not normalized_phrase:
        return False

    return f" {normalized_phrase} " in f" {text} "


def is_chechnya_address(address: Optional[str]) -> bool:
    """
    Возвращает True, когда адрес достаточно явно указывает на Чечню.

    Пустые и неоднозначные адреса не принимаются автоматически.
    """
    text = _normalize(address)

    if not text:
        return False

    # Если указан другой регион, не принимаем объявление,
    # даже если в описании упоминается Чечня.
    if any(
        _contains_phrase(text, region)
        for region in OTHER_REGION_MARKERS
    ):
        return False

    # Явное указание республики — сильный признак.
    if any(
        _contains_phrase(text, region)
        for region in REGION_MARKERS
    ):
        return True

    # Неоднозначные населённые пункты принимаем только
    # при явном указании Чеченской Республики.
    if any(
        _contains_phrase(text, locality)
        for locality in AMBIGUOUS_LOCALITIES
    ):
        return False

    # Проверяем характерные населённые пункты.
    return any(
        _contains_phrase(text, locality)
        for locality in CHECHNYA_LOCALITIES
    )


def is_chechnya_item(item) -> bool:
    """Проверяет адрес объявления Avito."""
    return is_chechnya_address(
        getattr(item, "address", None)
    )
