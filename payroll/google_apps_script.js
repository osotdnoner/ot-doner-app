/**
 * ОТ Донер — приём зарплатных данных из приложения в эту Google Таблицу.
 *
 * Как поставить (один раз):
 *  1. Откройте зарплатную таблицу → Расширения → Apps Script.
 *  2. Сотрите всё в окне и вставьте этот код целиком.
 *  3. В строке SECRET впишите своё секретное слово (любое, латиницей, например otdoner-2026-kassa).
 *     Это же слово впишите на Render в переменную GOOGLE_SHEET_SECRET.
 *  4. Нажмите «Сохранить» (значок дискеты).
 *  5. «Развертывание» → «Новое развертывание» → шестерёнка → «Веб-приложение».
 *     Выполнять от имени: «Я». У кого есть доступ: «Все». → «Развернуть».
 *  6. Google попросит разрешение: «Предоставить доступ» → ваш аккаунт →
 *     «Дополнительные настройки» → «Перейти на страницу …» → «Разрешить».
 *  7. Скопируйте «URL веб-приложения» (заканчивается на /exec) и вставьте его на Render
 *     в переменную GOOGLE_SHEET_URL.
 *
 * Приложение присылает строки: одна строка = один человек в одной смене.
 * Они попадают на лист «Данные из приложения». Если строка уже есть (админ поправил опоздание,
 * шеф снял штраф), она обновляется, а не дублируется. Остальные листы скрипт не трогает.
 */

const SECRET = "ВПИШИТЕ_СЕКРЕТНОЕ_СЛОВО";
const SHEET_NAME = "Данные из приложения";

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000); // две отправки одновременно не перепутают строки
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) {
      return reply({ ok: false, error: "Секретное слово не совпадает: проверьте GOOGLE_SHEET_SECRET и SECRET в скрипте." });
    }
    const sheet = getSheet(data.headers);
    const last = sheet.getLastRow();
    const keys = last > 1 ? sheet.getRange(2, 1, last - 1, 1).getValues().map((r) => String(r[0])) : [];
    let added = 0;
    let updated = 0;
    data.rows.forEach((row) => {
      const index = keys.indexOf(String(row[0]));
      if (index >= 0) {
        sheet.getRange(index + 2, 1, 1, row.length).setValues([row]);
        updated++;
      } else {
        sheet.getRange(keys.length + 2, 1, 1, row.length).setValues([row]);
        keys.push(String(row[0]));
        added++;
      }
    });
    return reply({ ok: true, added: added, updated: updated });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function getSheet(headers) {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = book.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = book.insertSheet(SHEET_NAME);
  }
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight("bold");
  sheet.setFrozenRows(1);
  return sheet;
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** Проверка: откройте «URL веб-приложения» в браузере — появится «Скрипт ОТ Донер работает». */
function doGet() {
  return reply({ ok: true, message: "Скрипт ОТ Донер работает. Данные приходят на лист «" + SHEET_NAME + "»." });
}
