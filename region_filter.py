
import re
import unicodedata
from typing import Optional

# Явные обозначения региона
REGION_MARKERS = (
    "чеченская республика",
    "чеченская респ",
    "республика чечня",
    "чечня",
)

# Населённые пункты Чечни.
# Для неоднозначных названий требуется также указание региона.
CHECHNYA_LOCALITIES = (
    "грозный",
    "аргун",
    "гудермес",
    "шали",
    "урус-мартан",
    "ачхой-мартан",
    "наурская",
    "знаменское",
    "серноводское",
    "курчалой",
    "шелковская",
    "гойты",
    "гикало",
    "надтеречное",
    "бачиюрт",
    "итум-кали",
    "ведено",
    "ножай-юрт",
    "старые атаги",
    "алхан-юрт",
    "ассиновская",
    "аллерой",
    "толстой-юрт",
    "чири-юрт",
    "сержень-юрт",
    "белгатой",
    "цоци-юрт",
    "мескер-юрт",
    "катыр-юрт",
    "самашки",
    "гехи",
    "гехи-чу",
    "бамут",
    "шалажи",
    "кулары",
    "петропавловская",
    "пригородное",
)

# Известные обозначения других регионов.
OTHER_REGION_MARKERS = (
    "москва",
    "московская область",
    "санкт-петербург",
    "дагестан",
    "республика дагестан",
    "ингушетия",
    "республика ингушетия",
    "ставропольский край",
    "кабардино-балкария",
    "северная осетия",
    "ростовская область",
    "краснодарский край",
)

def _normalize(value: Optional[str]) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("ё", "е").replace("—", "-").replace("–", "-")
    value = re.sub(r"[^\w\s-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def is_chechnya_address(address: Optional[str]) -> bool:
    """True только для явно распознанного адреса в Чечне."""
    text = _normalize(address)
    if not text:
        return False

    # Если адрес явно указывает другой регион — отклоняем.
    if any(_normalize(region) in text for region in OTHER_REGION_MARKERS):
        return False

    # Явное название республики достаточно.
    if any(_normalize(region) in text for region in REGION_MARKERS):
        return True

    # Иначе проверяем отдельные слова, а не подстроки.
    words = set(text.split())
    padded = f" {text} "
    for locality in CHECHNYA_LOCALITIES:
        locality = _normalize(locality)
        if f" {locality} " in padded:
            return True

    return False

def is_chechnya_item(item) -> bool:
    """Проверка объекта объявления с полем address."""
    return is_chechnya_address(getattr(item, "address", None))
