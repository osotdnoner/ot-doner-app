// Чек-листы и задачи: статусы Норма / Отклонение / Критично, сотрудники, закрытие задач.
// Каждый ответ сразу уходит на сервер; после ответа страница обновляется на том же месте.
(function () {
  const csrfInput = document.querySelector("[name=csrfmiddlewaretoken]");
  if (!csrfInput) return;
  const csrf = csrfInput.value;
  const scrollKey = "ot-scroll:" + location.pathname;

  const savedScroll = sessionStorage.getItem(scrollKey);
  if (savedScroll !== null) {
    window.scrollTo(0, Number(savedScroll));
    sessionStorage.removeItem(scrollKey);
  }

  function reloadKeepingScroll() {
    sessionStorage.setItem(scrollKey, String(window.scrollY));
    location.reload();
  }

  async function post(url, body) {
    try {
      const response = await fetch(url, {
        method: "POST", body, headers: { "X-CSRFToken": csrf }, credentials: "same-origin",
      });
      return await response.json();
    } catch (e) {
      return { ok: false, message: "Нет связи с сервером. Проверьте интернет и попробуйте снова." };
    }
  }

  function showError(box, text) {
    box.textContent = text;
    box.hidden = false;
  }

  // Добавляет фото к ответу, если оно нужно. false — человек передумал или камера недоступна.
  async function attachPhoto(body, needed, title, errorBox) {
    if (!needed) return true;
    try {
      const blob = await OTCamera.capture({ title, facing: "environment" });
      body.append("photo", blob, "photo.jpg");
      return true;
    } catch (e) {
      if (!e.cancelled) showError(errorBox, e.message);
      return false;
    }
  }

  function setBusy(form, busy) {
    form.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
  }

  // ---- Пункты чек-листа ----
  document.querySelectorAll(".item-form").forEach((form) => {
    const errorBox = form.querySelector(".item-error");
    const panel = form.querySelector(".problem-panel");
    const saveBtn = form.querySelector(".submit-problem");
    const label = form.dataset.label;

    async function submit(status, needPhoto) {
      errorBox.hidden = true;
      const body = new FormData(form);
      body.set("status", status);
      if (!(await attachPhoto(body, needPhoto, label, errorBox))) return;
      setBusy(form, true);
      const result = await post(form.dataset.url, body);
      if (result.ok) { reloadKeepingScroll(); return; }
      setBusy(form, false);
      showError(errorBox, result.message);
    }

    form.querySelectorAll("[data-status]").forEach((button) => {
      button.addEventListener("click", () => {
        const status = button.dataset.status;
        form.querySelectorAll("[data-status]").forEach((b) => b.classList.toggle("active", b === button));
        if (status === "ok") {
          panel.hidden = true;
          submit("ok", form.dataset.photoOk === "1");
          return;
        }
        form.dataset.status = status;
        panel.hidden = false;
        panel.classList.toggle("critical", status === "critical");
        saveBtn.textContent = form.dataset.photoProblem === "1" ? "Сфотографировать и сохранить" : "Сохранить";
      });
    });

    saveBtn.addEventListener("click", () => submit(form.dataset.status, form.dataset.photoProblem === "1"));
  });

  // ---- Сотрудники: приход и униформа ----
  document.querySelectorAll(".staff-form").forEach((form) => {
    const errorBox = form.querySelector(".item-error");
    const uniformPart = form.querySelector(".uniform-part");
    const missingPart = form.querySelector(".missing-part");

    function update() {
      const attendance = (form.querySelector("[name=attendance]:checked") || {}).value;
      const uniform = (form.querySelector("[name=uniform]:checked") || {}).value;
      uniformPart.hidden = !attendance || attendance === "absent";
      missingPart.hidden = uniform !== "violation";
    }
    form.addEventListener("change", update);
    update();

    form.querySelector(".save-staff").addEventListener("click", async () => {
      errorBox.hidden = true;
      const body = new FormData(form);
      const needPhoto = body.get("attendance") !== "absent"
        && (body.get("uniform") === "violation" || body.get("not_admitted") === "1");
      if (!(await attachPhoto(body, needPhoto, form.dataset.label, errorBox))) return;
      setBusy(form, true);
      const result = await post(form.dataset.url, body);
      if (result.ok) { reloadKeepingScroll(); return; }
      setBusy(form, false);
      showError(errorBox, result.message);
    });
  });

  // ---- «Изменить ответ» ----
  document.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById(button.dataset.edit).hidden = false;
      button.hidden = true;
    });
  });

  // ---- Закрытие задачи: только с фото «после» ----
  document.querySelectorAll(".task-close").forEach((button) => {
    button.addEventListener("click", async () => {
      const errorBox = button.parentElement.querySelector(".item-error");
      errorBox.hidden = true;
      const body = new FormData();
      if (!(await attachPhoto(body, true, "Фото «после»", errorBox))) return;
      button.disabled = true;
      const result = await post(button.dataset.url, body);
      if (result.ok) { reloadKeepingScroll(); return; }
      button.disabled = false;
      showError(errorBox, result.message);
    });
  });
})();
