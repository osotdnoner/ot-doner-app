// Кнопка «Я пришёл на смену»: (геолокация, если точка её требует) → селфи → отправка на сервер.
// Решение принимает сервер: телефон только присылает снимок и, при необходимости, координаты.
(function () {
  const button = document.getElementById("checkin-btn");
  const statusBox = document.getElementById("status");
  const pointSelect = document.getElementById("point-select");
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const locationPoints = (button.dataset.locationPoints || "").split(",").filter(Boolean);

  function showStatus(text, kind) {
    statusBox.textContent = text;
    statusBox.className = "alert" + (kind ? " alert-" + kind : "");
    statusBox.hidden = false;
  }

  function setBusy(busy, label) {
    button.disabled = busy;
    button.textContent = busy ? label : "Я пришёл на смену";
  }

  function getPosition() {
    return new Promise((resolve, reject) => {
      if (!("geolocation" in navigator)) {
        reject(new Error("Этот браузер не умеет определять местоположение."));
        return;
      }
      navigator.geolocation.getCurrentPosition(resolve, (err) => {
        const messages = {
          1: "Нет доступа к геолокации. Разрешите её для этого сайта в настройках браузера и попробуйте снова.",
          2: "Не удалось определить местоположение. Включите геолокацию (GPS) на телефоне.",
          3: "Местоположение определяется слишком долго. Подойдите ближе к окну и попробуйте снова.",
        };
        reject(new Error(messages[err.code] || "Не удалось определить местоположение."));
      }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
    });
  }

  async function send(pointId, position, photo) {
    const body = new FormData();
    if (pointId) body.append("point", pointId);
    if (position) {
      body.append("latitude", position.coords.latitude);
      body.append("longitude", position.coords.longitude);
      body.append("accuracy", position.coords.accuracy);
    }
    body.append("selfie", photo, "selfie.jpg");
    const response = await fetch(button.dataset.url, {
      method: "POST",
      body,
      headers: { "X-CSRFToken": csrf },
      credentials: "same-origin",
    });
    return response.json();
  }

  button.addEventListener("click", async () => {
    statusBox.hidden = true;
    const pointId = pointSelect ? pointSelect.value : button.dataset.point;
    if (pointSelect && !pointId) {
      showStatus("Сначала выберите точку, на которой вы сегодня работаете.", "warn");
      return;
    }
    const needLocation = locationPoints.includes(String(pointId));
    let position = null, photo;
    try {
      if (needLocation) {
        setBusy(true, "Определяем местоположение…");
        position = await getPosition();
      }
      setBusy(true, "Делаем селфи…");
      photo = await OTCamera.capture({ title: "Селфи для отметки", facing: "user" });
    } catch (e) {
      if (!e.cancelled) showStatus(e.message, "error");
      setBusy(false);
      return;
    }
    setBusy(true, "Проверяем отметку…");
    try {
      const result = await send(pointId, position, photo);
      if (["ok", "late", "already"].includes(result.status)) {
        window.location.reload();
        return;
      }
      showStatus(result.message, result.status === "low_accuracy" ? "warn" : "error");
    } catch (e) {
      showStatus("Нет связи с сервером. Проверьте интернет и попробуйте снова.", "error");
    }
    setBusy(false);
  });
})();
