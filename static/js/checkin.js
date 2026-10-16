// Кнопка «Я пришёл на смену»: геолокация → селфи → отправка на сервер.
// Решение принимает сервер: телефон только сообщает, где он, и присылает снимок.
(function () {
  const button = document.getElementById("checkin-btn");
  const statusBox = document.getElementById("status");
  const sheet = document.getElementById("camera");
  const video = document.getElementById("video");
  const canvas = document.getElementById("canvas");
  const snapBtn = document.getElementById("snap");
  const cancelBtn = document.getElementById("cancel");
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;

  let position = null;
  let stream = null;

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

  async function openCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Этот браузер не даёт доступ к камере. Откройте приложение в Chrome или Safari.");
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 720 }, height: { ideal: 960 } },
        audio: false,
      });
    } catch (e) {
      throw new Error("Нет доступа к камере. Разрешите её для этого сайта в настройках браузера.");
    }
    video.srcObject = stream;
    sheet.hidden = false;
  }

  function closeCamera() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    sheet.hidden = true;
  }

  function takePhoto() {
    // Снимок только с живой камеры — загрузить фото из галереи нельзя
    const maxWidth = 720;
    const scale = Math.min(1, maxWidth / video.videoWidth);
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.82));
  }

  async function send(photo) {
    const body = new FormData();
    body.append("latitude", position.coords.latitude);
    body.append("longitude", position.coords.longitude);
    body.append("accuracy", position.coords.accuracy);
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
    setBusy(true, "Определяем местоположение…");
    try {
      position = await getPosition();
      await openCamera();
    } catch (e) {
      showStatus(e.message, "error");
      setBusy(false);
    }
  });

  cancelBtn.addEventListener("click", () => {
    closeCamera();
    setBusy(false);
  });

  snapBtn.addEventListener("click", async () => {
    snapBtn.disabled = true;
    try {
      const photo = await takePhoto();
      closeCamera();
      setBusy(true, "Проверяем отметку…");
      const result = await send(photo);
      if (["ok", "late", "already"].includes(result.status)) {
        window.location.reload();
        return;
      }
      showStatus(result.message, result.status === "low_accuracy" ? "warn" : "error");
    } catch (e) {
      showStatus("Нет связи с сервером. Проверьте интернет и попробуйте снова.", "error");
    } finally {
      snapBtn.disabled = false;
      setBusy(false);
    }
  });
})();
