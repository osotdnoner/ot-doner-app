from django import forms

MAX_SELFIE_BYTES = 5 * 1024 * 1024


class CheckinForm(forms.Form):
    """То, что телефон присылает при нажатии «Я пришёл»."""

    # Координаты присылаются, только если у точки включена проверка территории
    latitude = forms.FloatField(min_value=-90, max_value=90, required=False)
    longitude = forms.FloatField(min_value=-180, max_value=180, required=False)
    accuracy = forms.FloatField(min_value=0, required=False)
    point = forms.IntegerField(required=False)
    selfie = forms.ImageField(error_messages={"required": "Без селфи отметиться нельзя."})

    def clean_selfie(self):
        selfie = self.cleaned_data["selfie"]
        if selfie.size > MAX_SELFIE_BYTES:
            raise forms.ValidationError("Фото слишком большое.")
        return selfie


class LateReasonForm(forms.Form):
    reason = forms.CharField(
        label="Причина опоздания",
        min_length=3,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Например: пробка на Абая, автобус опоздал"}),
        error_messages={"required": "Напишите причину опоздания.", "min_length": "Напишите причину подробнее."},
    )
