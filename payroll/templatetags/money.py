from django import template

register = template.Library()


@register.filter
def money(value) -> str:
    """3000 → «3 000 ₸» (с неразрывными пробелами, чтобы сумма не разрывалась на две строки)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return value
    return f"{number:,}".replace(",", " ") + " ₸"
