// Общая камера приложения: снимок только с живой камеры, без доступа к галерее.
// Использование: const blob = await OTCamera.capture({ title: "Санузел", facing: "environment" });
window.OTCamera = (function () {
  let sheet, video, canvas, title, snapBtn, cancelBtn;
  let stream = null;
  let pending = null;

  function init() {
    if (sheet) return;
    sheet = document.getElementById("camera");
    video = document.getElementById("video");
    canvas = document.getElementById("canvas");
    title = document.getElementById("camera-title");
    snapBtn = document.getElementById("snap");
    cancelBtn = document.getElementById("cancel");
    snapBtn.addEventListener("click", snap);
    cancelBtn.addEventListener("click", cancel);
  }

  function close() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    sheet.hidden = true;
  }

  async function capture(options) {
    init();
    const facing = (options && options.facing) || "environment";
    title.textContent = (options && options.title) || "Фото";
    video.classList.toggle("mirror", facing === "user");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Этот браузер не даёт доступ к камере. Откройте приложение в Chrome или Safari.");
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
    } catch (e) {
      throw new Error("Нет доступа к камере. Разрешите её для этого сайта в настройках браузера.");
    }
    video.srcObject = stream;
    sheet.hidden = false;
    return new Promise((resolve, reject) => { pending = { resolve, reject }; });
  }

  function snap() {
    const maxSide = 1024;
    const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      close();
      if (pending) pending.resolve(blob);
      pending = null;
    }, "image/jpeg", 0.82);
  }

  function cancel() {
    close();
    if (pending) {
      const error = new Error("cancelled");
      error.cancelled = true;
      pending.reject(error);
    }
    pending = null;
  }

  return { capture };
})();
