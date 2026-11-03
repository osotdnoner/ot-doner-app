/** @OnlyCurrentDoc */
/**
 * ОТ Донер — зарплатная таблица.
 *
 * Этот скрипт живёт внутри Google Таблицы. Он:
 *  1) строит листы «Итоги», «Выплата», «Ставки», «Сотрудники», «Журнал», «Как пользоваться» (функция setup);
 *  2) принимает данные из приложения (функция doPost): строки — на лист «Журнал»,
 *     список сотрудников — на лист «Сотрудники».
 *
 * Строки «Журнала» двух видов:
 *  • «Смена» — один человек в одной смене: филиал, смена (день, ночь или полная), выход, опоздание,
 *    переработка и недоработка в часах, штрафы, бонусы, замена;
 *  • «Аванс» — деньги, выданные заранее.
 * Если админ поправил отметку или шеф снял штраф, строка обновляется. «Выходной», удалённая замена
 * или удалённый аванс — строка удаляется.
 *
 * «Ставки» — сколько платим за смену по должности в каждом филиале (донерщик на 27 мкрн — столько-то).
 * Шеф заполняет жёлтые клетки один раз; каждая отмеченная смена считается сама.
 * «Сотрудники» — список от приложения: родной филиал, должность, Kaspi Gold. Там же «своя ставка» —
 * только для тех, кому платят не как всем на их должности.
 * Цену часа, деньги за часы, начислено, к выплате и итоги считают формулы.
 * «Выплата» — готовый список для бухгалтера: кому, сколько и на какой Kaspi Gold.
 *
 * Если скрипт обновили (новая раскладка), выполните rebuild: листы перестроятся, а приложение
 * отправит все данные заново. Ставки сохраняются.
 * Обычно достаточно выполнить setup: он добавляет новые листы и обновляет формулы, данные не трогает.
 *
 * Подключение (один раз):
 *  1. Таблица → Расширения → Apps Script → вставить этот код целиком.
 *  2. В строке SECRET вписать секретное слово (то же — на сервере в GOOGLE_SHEET_SECRET).
 *  3. Сохранить, выбрать функцию setup → «Выполнить» → разрешить доступ.
 *  4. «Развертывание» → «Новое развертывание» → «Веб-приложение»:
 *     выполнять от имени «Я», доступ «Все» → «Развернуть».
 *  5. «URL веб-приложения» (…/exec) вписать на сервере в GOOGLE_SHEET_URL.
 */

const SECRET = "ВПИШИТЕ_СЕКРЕТНОЕ_СЛОВО";

const DATA = "Журнал";
const OLD_DATA = ["Смены"]; // старые названия листа с данными
const RATES = "Ставки";      // по должностям в каждом филиале
const STAFF = "Сотрудники";  // список сотрудников, Kaspi Gold, своя ставка
const TOTALS = "Итоги";
const PAYOUT = "Выплата";
const HELP = "Как пользоваться";

// Колонки, которые присылает приложение (A–Z). Порядок совпадает с payroll/sheets.py.
const HEADERS = [
  "Ключ", "Дата", "Филиал", "Что", "Смена", "Сотрудник", "Родной филиал", "Должность", "Выход", "Пришёл в",
  "Опоздание, мин", "Причина", "Переработка, ч", "Недоработка, ч", "Часы: почему", "Штраф, ₸", "За что штраф",
  "Бонус, ₸", "За что бонус", "Аванс, ₸", "Замена", "Комментарий", "Кто отметил", "Обновлено", "Часов в смене",
  "Код сотрудника",
];
// Что считают формулы (AA–AH): ставка, цена часа, за часы, начислено, к выплате, смен, опозданий, невыходов

// Лист «Ставки»: A ключ «филиал|должность» (скрыт) · B филиал · C должность · D день · E ночь · F полная
const RATE_HEADERS = ["Ключ", "Филиал", "Должность", "День, ₸", "Ночь, ₸", "Полная смена, ₸"];

// Лист «Сотрудники»: A код (скрыт) · B сотрудник · C родной филиал · D должность · E–G своя ставка ·
// H статус · I Kaspi Gold · J имя в Kaspi
const STAFF_HEADERS = [
  "Код", "Сотрудник", "Родной филиал", "Должность", "Своя ставка: день, ₸", "Своя ставка: ночь, ₸",
  "Своя ставка: полная, ₸", "Статус", "Kaspi Gold", "Имя в Kaspi",
];

// Филиалы и их должности — для итогов по филиалам и первого заполнения «Ставок».
// Новые сочетания филиал + должность скрипт добавит в «Ставки» сам, когда придут данные.
const BRANCHES = ["27 мкрн", "16 мкрн", "17 мкрн", "14 мкрн"];
const BRANCH_POSITIONS = {
  "27 мкрн": ["Администратор", "Донерщик", "Пиццерист", "Кассир", "Раннер доставки", "Раннер кассы", "Техперсонал"],
  "16 мкрн": ["Администратор", "Донерщик", "Кассир"],
  "17 мкрн": ["Администратор", "Донерщик", "Пиццерист", "Кассир", "Раннер кассы", "Техперсонал кухни",
    "Техперсонал зала"],
  "14 мкрн": ["Администратор", "Донерщик", "Пиццерист", "Кассир", "Раннер кассы", "Техперсонал"],
};

const MONEY = "#,##0";
const INPUT_BG = "#fff6d6";
const HEAD_BG = "#c2410c";

// ---------------------------------------------------------------- приём данных

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000); // две отправки одновременно не перепутают строки
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) {
      return reply({ ok: false, error: "Секретное слово не совпадает: проверьте GOOGLE_SHEET_SECRET и SECRET в скрипте." });
    }
    const book = SpreadsheetApp.getActiveSpreadsheet();
    if (!book.getSheetByName(DATA)) setup();

    // 0. Список сотрудников → «Сотрудники» (имя, филиал, должность, Kaspi; ставки не трогаем)
    const staffAdded = data.staff ? updateStaff(book, data.staff, true) : 0;
    if (data.staff) addPositionRates(book, data.staff.map((s) => [s[2], s[3]]));

    const sheet = book.getSheetByName(DATA);
    const headers = data.headers || HEADERS;
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

    // 1. Удаления («Выходной», удалённые замена или аванс): снизу вверх, чтобы номера строк не сдвигались
    const removed = (data.delete || []).length ? removeRows(sheet, data.delete) : 0;

    // 2. Новые и изменённые строки
    let keys = readKeys(sheet);
    let added = 0;
    let updated = 0;
    (data.rows || []).forEach((row) => {
      const values = row.slice();
      values[1] = toSerialDate(values[1]);
      const index = keys.indexOf(String(values[0]));
      let r;
      if (index >= 0) {
        r = index + 2;
        updated++;
      } else {
        r = keys.length + 2;
        if (r > sheet.getMaxRows()) sheet.insertRowsAfter(sheet.getMaxRows(), 200);
        keys.push(String(values[0]));
        added++;
      }
      sheet.getRange(r, 1, 1, values.length).setValues([values]);
      sheet.getRange(r, 2).setNumberFormat("dd.mm.yyyy");
    });

    // 3. Если человека ещё нет в «Сотрудниках» (список не дошёл) — добавить по строке журнала
    const fromRows = (data.rows || []).map((row) => [row[25], row[5], row[6], row[7], ""]).filter((s) => s[0]);
    updateStaff(book, fromRows, false);
    // 4. Новое сочетание филиал + должность (например, повар горячего цеха) — строка в «Ставках»
    addPositionRates(book, (data.rows || []).filter((row) => row[3] === "Смена").map((row) => [row[2], row[7]]));
    return reply({ ok: true, added: added, updated: updated, removed: removed, staff: staffAdded });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return reply({ ok: true, message: "Скрипт ОТ Донер работает. Данные приходят на лист «" + DATA + "»." });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function readKeys(sheet) {
  const last = sheet.getLastRow();
  return last > 1 ? sheet.getRange(2, 1, last - 1, 1).getValues().map((r) => String(r[0])) : [];
}

function removeRows(sheet, keysToDelete) {
  const keys = readKeys(sheet);
  const rows = [];
  keysToDelete.forEach((key) => {
    const index = keys.indexOf(String(key));
    if (index >= 0) rows.push(index + 2);
  });
  rows.sort((a, b) => b - a).forEach((r) => sheet.deleteRow(r));
  return rows.length;
}

/** «2026-10-16» → число-дата Google (без сдвига из-за часовых поясов). */
function toSerialDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
  if (!m) return value;
  return (Date.UTC(+m[1], +m[2] - 1, +m[3]) - Date.UTC(1899, 11, 30)) / 86400000;
}

/**
 * Ставки по должностям: добавить недостающие строки «филиал | должность» (пустые — шеф впишет сумму).
 * pairs — [филиал, должность]. «Резерв» не филиал: выручалка получает ставку того филиала, где работала.
 */
function addPositionRates(book, pairs) {
  const sheet = book.getSheetByName(RATES);
  if (!sheet || !pairs.length) return 0;
  const last = sheet.getLastRow();
  const existing = last > 1 ? sheet.getRange(2, 2, last - 1, 2).getValues().map((r) => r[0] + "|" + r[1]) : [];
  const fresh = [];
  pairs.forEach((pair) => {
    const branch = String(pair[0] || ""), position = String(pair[1] || "");
    if (!branch || !position || branch === "Резерв") return;
    const key = branch + "|" + position;
    if (existing.indexOf(key) >= 0) return;
    existing.push(key);
    fresh.push([branch, position, "", "", ""]);
  });
  if (!fresh.length) return 0;
  sheet.getRange(last + 1, 2, fresh.length, 5).setValues(fresh);
  sheet.getRange(2, 2, last - 1 + fresh.length, 5).sort([{ column: 2, ascending: true }, { column: 3, ascending: true }]);
  return fresh.length;
}

/**
 * Список сотрудников на листе «Сотрудники». staff — строки [код, имя, родной филиал, должность, статус, Kaspi, имя в Kaspi].
 * overwrite: обновлять ли имя, филиал, должность, статус и Kaspi у тех, кто уже есть. Своя ставка (E–G) не трогается.
 * Возвращает, сколько людей добавлено.
 */
function updateStaff(book, staff, overwrite) {
  const sheet = book.getSheetByName(STAFF);
  if (!sheet || !staff.length) return 0;
  const last = sheet.getLastRow();
  const n = Math.max(last - 1, 0);
  const codes = n ? sheet.getRange(2, 1, n, 1).getValues().map((r) => String(r[0])) : [];
  const info = n ? sheet.getRange(2, 2, n, 3).getValues() : [];
  const extra = n ? sheet.getRange(2, 8, n, 3).getValues() : []; // статус, Kaspi, имя в Kaspi
  const fresh = [];
  let changed = false;
  staff.forEach((s) => {
    const code = String(s[0]);
    const i = codes.indexOf(code);
    if (i < 0) {
      codes.push(code);
      fresh.push([code, s[1], s[2], s[3], "", "", "", s[4] || "работает", s[5] || "", s[6] || ""]);
    } else if (overwrite) {
      info[i] = [s[1], s[2], s[3]];
      extra[i] = [s[4] || "", s[5] || "", s[6] || ""];
      changed = true;
    }
  });
  if (changed) {
    sheet.getRange(2, 2, n, 3).setValues(info);
    sheet.getRange(2, 8, n, 3).setValues(extra);
  }
  if (fresh.length) sheet.getRange(last + 1, 1, fresh.length, STAFF_HEADERS.length).setValues(fresh);
  if (fresh.length || changed) { // по филиалу и имени: новый сотрудник встаёт к своим
    sheet.getRange(2, 1, n + fresh.length, STAFF_HEADERS.length)
      .sort([{ column: 3, ascending: true }, { column: 2, ascending: true }]);
  }
  return fresh.length;
}

// ---------------------------------------------------------------- формулы под язык таблицы

// В русской таблице аргументы формул разделяются «;», в английской — «,». Скрипт пишет формулы
// с запятыми и сам переводит их под язык таблицы (запятые внутри кавычек не трогает).
let SEPARATOR = null;

function separator() {
  if (SEPARATOR) return SEPARATOR;
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const probe = book.insertSheet("проверка_" + Date.now());
  probe.getRange("A1").setFormula("=SUM(1,2)");
  SpreadsheetApp.flush();
  SEPARATOR = probe.getRange("A1").getDisplayValue() === "3" ? "," : ";";
  book.deleteSheet(probe);
  return SEPARATOR;
}

function F(formula) {
  if (separator() === ",") return formula;
  let out = "";
  let inString = false;
  for (const ch of formula) {
    if (ch === '"') inString = !inString;
    out += !inString && ch === "," ? ";" : ch;
  }
  return out;
}

/**
 * Перестроить листы под новую версию скрипта. Журнал и итоги строятся заново — приложение отправит
 * все отметки, авансы и список сотрудников ещё раз (команда sync_sheet). «Ставки» и «Сотрудники» сохраняются.
 */
function rebuild() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.insertSheet("временный"); // в таблице всегда должен оставаться хотя бы один лист
  [TOTALS, PAYOUT, DATA, HELP].concat(OLD_DATA).forEach((name) => {
    const s = book.getSheetByName(name);
    if (s) book.deleteSheet(s);
  });
  setup(); // setup сам уберёт временный лист
}

/**
 * Переход с личных ставок на ставки по должностям: прежний лист «Ставки» (список сотрудников с личными
 * ставками и Kaspi) становится листом «Сотрудники» — вписанные суммы остаются как «своя ставка».
 */
function migrateRates(book) {
  const old = book.getSheetByName(RATES);
  if (!old || book.getSheetByName(STAFF) || old.getRange("B1").getValue() !== "Сотрудник") return;
  old.setName(STAFF);
  // строки «другая должность» (код «E5|Раннер кассы») больше не нужны: ставку даёт должность на смене
  const last = old.getLastRow();
  if (last > 1) {
    const codes = old.getRange(2, 1, last - 1, 1).getValues();
    for (let i = codes.length - 1; i >= 0; i--) {
      if (String(codes[i][0]).indexOf("|") >= 0) old.deleteRow(i + 2);
    }
  }
}

// ---------------------------------------------------------------- построение таблицы

function setup() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.setSpreadsheetTimeZone("Asia/Almaty");
  migrateRates(book);
  buildStaff(book);
  buildRates(book);
  buildData(book);
  buildTotals(book);
  buildPayout(book);
  buildHelp(book);
  ["Лист1", "Sheet1", "Лист 1", "временный"].forEach((name) => {
    const s = book.getSheetByName(name);
    if (s && s.getLastRow() === 0 && book.getSheets().length > 1) book.deleteSheet(s);
  });
  [TOTALS, PAYOUT, RATES, STAFF, DATA, HELP].forEach((name, i) => {
    book.setActiveSheet(book.getSheetByName(name));
    book.moveActiveSheet(i + 1);
  });
  book.setActiveSheet(book.getSheetByName(TOTALS));
}

function sheetNamed(book, name) {
  return book.getSheetByName(name) || book.insertSheet(name);
}

function styleHeader(range) {
  range.setFontWeight("bold").setBackground(HEAD_BG).setFontColor("#ffffff").setVerticalAlignment("middle").setWrap(true);
}

/**
 * Формулы журнала (строка заголовков, колонки AA–AI). Ставятся и при постройке, и при обновлении
 * скрипта (setup) — данные при этом не трогаются.
 */
function journalFormulas(sheet) {
  const arr = (title, body) => '={"' + title + '";ARRAYFORMULA(IF(A2:A="",,' + body + '))}';
  // Ставка за день, ночь и полную смену для каждой строки (служебные колонки AI–AK):
  // своя ставка человека с «Сотрудников» (по коду Z), а где она пустая — ставка по должности
  // с «Ставок» (по филиалу, где он работал, C, и должности в эту смену, H). Пусто или текст — 0.
  const own = (col) => "IFERROR(VLOOKUP(Z2:Z,'" + STAFF + "'!A:G," + col + ",0)*1,0)";
  const pos = (col) => "IFERROR(VLOOKUP(C2:C&\"|\"&H2:H,'" + RATES + "'!A:F," + col + ",0)*1,0)";
  sheet.getRange("AI1").setFormula(F(arr("Ставка: день", "IF(" + own(5) + ">0," + own(5) + "," + pos(4) + ")")));
  sheet.getRange("AJ1").setFormula(F(arr("Ставка: ночь", "IF(" + own(6) + ">0," + own(6) + "," + pos(5) + ")")));
  // Полная: своя полная → полная по должности (если своей дневной и ночной нет) → день + ночь
  sheet.getRange("AK1").setFormula(F(arr("Ставка: полная",
    "IF(" + own(7) + ">0," + own(7) + ",IF((" + own(5) + "+" + own(6) + "=0)*(" + pos(6) + ">0)," + pos(6) +
    ",AI2:AI+AJ2:AJ))"
  )));
  // Ставка этой смены — по виду смены (E). Авансам и невыходам — 0.
  sheet.getRange("AA1").setFormula(F(arr("Ставка, ₸",
    'IF((D2:D<>"Смена")+(I2:I="Не вышел"),0,IF(E2:E="День",AI2:AI,IF(E2:E="Ночь",AJ2:AJ,AK2:AK)))'
  )));
  // Цена часа = ставка ÷ часы смены; за часы = (переработка − недоработка) × цена часа
  sheet.getRange("AB1").setFormula(F(arr("Цена часа, ₸", "IF(Y2:Y>0,AA2:AA/Y2:Y,0)")));
  sheet.getRange("AC1").setFormula(F(arr("За часы, ₸", "ROUND((M2:M-N2:N)*AB2:AB)")));
  sheet.getRange("AD1").setFormula(F(arr("Начислено, ₸", "AA2:AA+AC2:AC-P2:P+R2:R")));
  sheet.getRange("AE1").setFormula(F(arr("К выплате, ₸", "AD2:AD-T2:T")));
  sheet.getRange("AF1").setFormula(F(arr("Смен", 'IF((D2:D="Смена")*(I2:I<>"Не вышел"),1,0)')));
  sheet.getRange("AG1").setFormula(F(arr("Опозданий", 'IF(I2:I="Опоздал",1,0)')));
  sheet.getRange("AH1").setFormula(F(arr("Невыходов", 'IF(I2:I="Не вышел",1,0)')));
  styleHeader(sheet.getRange("AI1:AK1"));
  sheet.getRange("AI1:AK1").setBackground("#7c2d12");
  sheet.hideColumns(35, 3); // ставки дня, ночи и полной — служебные
}

function buildData(book) {
  const sheet = sheetNamed(book, DATA);
  if (sheet.getLastRow() > 0) {
    journalFormulas(sheet); // уже построен — данные не трогаем, только обновляем формулы
    return;
  }
  sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  journalFormulas(sheet);

  styleHeader(sheet.getRange("A1:AH1"));
  sheet.getRange("AA1:AH1").setBackground("#7c2d12");
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(1);
  sheet.hideColumns(1); // ключ строки нужен только скрипту
  sheet.hideColumns(26); // код сотрудника — для поиска ставки
  sheet.getRange("B2:B").setNumberFormat("dd.mm.yyyy");
  ["P2:P", "R2:R", "T2:T", "AA2:AE"].forEach((a1) => sheet.getRange(a1).setNumberFormat(MONEY));
  sheet.getRange("M2:N").setNumberFormat("0");
  sheet.getRange("Y2:Y").setNumberFormat("0.##");
  const widths = [85, 80, 60, 65, 160, 90, 120, 115, 65, 80, 160, 85, 85, 200, 75, 200, 75, 170, 80, 220, 160,
    110, 120, 70, 0, 80, 80, 80, 95, 95, 55, 75, 80];
  widths.forEach((w, i) => { if (w) sheet.setColumnWidth(i + 2, w); });
  sheet.setRowHeight(1, 42);

  const rule = (formula, color) => SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied(formula)
    .setBackground(color).setRanges([sheet.getRange("B2:AH")]).build();
  sheet.setConditionalFormatRules([
    rule('=$I2="Не вышел"', "#fdecea"),
    rule('=$I2="Опоздал"', "#fff6e0"),
    rule('=$D2="Аванс"', "#e8f0fe"),
    rule('=$U2<>""', "#eaf7ee"),
  ]);
  sheet.getRange("A1:AH").createFilter();
  sheet.protect().setDescription("Заполняет приложение ОТ Донер").setWarningOnly(true);
}

function buildRates(book) {
  const sheet = sheetNamed(book, RATES);
  if (sheet.getLastRow() === 0) {
    sheet.getRange("A1").setFormula(F('={"Ключ";ARRAYFORMULA(IF(B2:B="",,B2:B&"|"&C2:C))}'));
    sheet.getRange(1, 2, 1, RATE_HEADERS.length - 1).setValues([RATE_HEADERS.slice(1)]);
    styleHeader(sheet.getRange(1, 1, 1, RATE_HEADERS.length));
    sheet.setRowHeight(1, 36);
    sheet.setFrozenRows(1);
    sheet.hideColumns(1);
    sheet.getRange("D2:F").setNumberFormat(MONEY).setBackground(INPUT_BG);
    sheet.setColumnWidth(2, 100);
    sheet.setColumnWidth(3, 170);
    sheet.setColumnWidths(4, 3, 120);
    sheet.getRange("D1").setNote("Сколько платим за одну дневную смену на этой должности в этом филиале.");
    sheet.getRange("E1").setNote("Сколько платим за одну ночную смену. Где ночной смены нет — оставьте пустым.");
    sheet.getRange("F1").setNote("Полная смена (с начала дневной до конца ночной). Пусто — считается день + ночь. " +
      "Если платите иначе, впишите свою сумму.");
    sheet.getRange("B1").setNote("Каждая смена в «Журнале» берёт ставку отсюда: по филиалу, где человек работал, " +
      "и должности, на которой он был в эту смену (кассир на раннере — ставка раннера). " +
      "Новые сочетания филиал + должность появятся здесь сами — впишите им сумму. " +
      "Если кому-то платите не как всем — впишите ему «свою ставку» на листе «Сотрудники».");
  }
  // Должности филиалов из списка и все сочетания из «Сотрудников»
  const pairs = [];
  BRANCHES.forEach((b) => (BRANCH_POSITIONS[b] || []).forEach((p) => pairs.push([b, p])));
  const staff = book.getSheetByName(STAFF);
  if (staff && staff.getLastRow() > 1) {
    staff.getRange(2, 3, staff.getLastRow() - 1, 2).getValues().forEach((r) => pairs.push([r[0], r[1]]));
  }
  addPositionRates(book, pairs);
}

function buildStaff(book) {
  const sheet = sheetNamed(book, STAFF);
  const fresh = sheet.getLastRow() === 0;
  sheet.getRange(1, 1, 1, STAFF_HEADERS.length).setValues([STAFF_HEADERS]);
  styleHeader(sheet.getRange(1, 1, 1, STAFF_HEADERS.length));
  sheet.setRowHeight(1, 42);
  sheet.setFrozenRows(1);
  sheet.getRange("E2:G").setNumberFormat(MONEY).setBackground("#fdf7ea");
  sheet.setColumnWidth(2, 170);
  sheet.setColumnWidth(3, 100);
  sheet.setColumnWidth(4, 160);
  sheet.setColumnWidths(5, 3, 110);
  sheet.setColumnWidth(8, 90);
  sheet.setColumnWidth(9, 140);
  sheet.setColumnWidth(10, 140);
  sheet.getRange("B1").setNote("Список ведёт приложение: новый сотрудник появляется здесь сам. " +
    "Имя, филиал, должность и Kaspi меняйте в приложении (панель «Сотрудники»).");
  sheet.getRange("E1").setNote("Только если этому человеку платите не как всем на его должности. " +
    "Пусто — ставка по должности с листа «Ставки».");
  sheet.getRange("I1").setNote("Kaspi Gold и имя получателя вписываются в приложении: панель → «Сотрудники» → карточка.");
  if (fresh) sheet.hideColumns(1);
}

function buildTotals(book) {
  const sheet = sheetNamed(book, TOTALS);
  if (sheet.getLastRow() > 0) return;
  sheet.getRange("A1:A4").setValues([["Месяц"], ["Период"], ["С"], ["По"]]).setFontWeight("bold");
  sheet.getRange("B1").setFormula(F("=EOMONTH(TODAY(),-1)+1")).setNumberFormat("mmmm yyyy").setBackground(INPUT_BG)
    .setNote("Любая дата нужного месяца. Сейчас стоит текущий месяц — впишите, например, 01.09.2026, чтобы посмотреть сентябрь.");
  const today = new Date().getDate();
  sheet.getRange("B2").setValue(today > 15 ? "16–конец месяца" : "1–15").setBackground(INPUT_BG).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(["1–15", "16–конец месяца", "Весь месяц"], true).build()
  );
  sheet.getRange("B3").setFormula(F('=IF(B2="16–конец месяца",DATE(YEAR(B1),MONTH(B1),16),DATE(YEAR(B1),MONTH(B1),1))'));
  sheet.getRange("B4").setFormula(F('=IF(B2="1–15",DATE(YEAR(B1),MONTH(B1),15),EOMONTH(B1,0))'));
  sheet.getRange("B3:B4").setNumberFormat("dd.mm.yyyy");

  // По филиалам: сколько начислено за работу на филиале и сколько авансов там выдано
  sheet.getRange("D1:F1").setValues([["Филиал", "Начислено, ₸", "Авансы, ₸"]]);
  styleHeader(sheet.getRange("D1:F1"));
  const period = ",'" + DATA + "'!B:B,\">=\"&$B$3,'" + DATA + "'!B:B,\"<=\"&$B$4)";
  BRANCHES.forEach((name, i) => {
    const r = i + 2;
    sheet.getRange(r, 4).setValue(name);
    sheet.getRange(r, 5).setFormula(F("=SUMIFS('" + DATA + "'!AD:AD,'" + DATA + "'!C:C,D" + r + period));
    sheet.getRange(r, 6).setFormula(F("=SUMIFS('" + DATA + "'!T:T,'" + DATA + "'!C:C,D" + r + period));
  });
  const totalRow = BRANCHES.length + 2;
  sheet.getRange(totalRow, 4).setValue("Всего").setFontWeight("bold");
  sheet.getRange(totalRow, 5).setFormula(F("=SUMIFS('" + DATA + "'!AD:AD" + period)).setFontWeight("bold");
  sheet.getRange(totalRow, 6).setFormula(F("=SUMIFS('" + DATA + "'!T:T" + period)).setFontWeight("bold");
  sheet.getRange(2, 5, BRANCHES.length + 1, 2).setNumberFormat(MONEY);

  // По каждому человеку за период: все его смены (и замены на других филиалах), часы и авансы
  const start = totalRow + 2;
  sheet.getRange(start, 1).setValue("По сотрудникам за период").setFontWeight("bold").setFontSize(12);
  const q = "select G, F, sum(AF), sum(AG), sum(AH), sum(M), sum(N), sum(AA), sum(AC), sum(R), sum(P), sum(AD), sum(T), sum(AE) " +
    "where A is not null and B >= date '\"&TEXT($B$3,\"yyyy-mm-dd\")&\"' and B <= date '\"&TEXT($B$4,\"yyyy-mm-dd\")&\"' " +
    "group by G, F order by G, F " +
    "label G 'Родной филиал', F 'Сотрудник', sum(AF) 'Смен', sum(AG) 'Опозданий', sum(AH) 'Невыходов', " +
    "sum(M) 'Переработка, ч', sum(N) 'Недоработка, ч', sum(AA) 'По ставкам, ₸', sum(AC) 'За часы, ₸', " +
    "sum(R) 'Бонусы, ₸', sum(P) 'Штрафы, ₸', sum(AD) 'Начислено, ₸', sum(T) 'Авансы, ₸', sum(AE) 'К выплате, ₸'";
  sheet.getRange(start + 1, 1).setFormula(F(
    "=IFERROR(QUERY('" + DATA + "'!A:AH,\"" + q + "\",1),\"За этот период отметок пока нет\")"
  ));
  styleHeader(sheet.getRange(start + 1, 1, 1, 14));
  sheet.getRange(start + 2, 8, 500, 7).setNumberFormat(MONEY);
  sheet.setColumnWidth(1, 110);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 120);
  sheet.setColumnWidth(4, 110);
  sheet.setColumnWidths(5, 10, 100);
}

/**
 * «Выплата» — список для бухгалтера за период, выбранный на «Итогах»: кому, сколько и на какой Kaspi Gold.
 * A код (скрыт) · B сотрудник · C родной филиал · D начислено · E авансы · F к выплате · G Kaspi Gold ·
 * H имя в Kaspi · I проверить
 */
function buildPayout(book) {
  const sheet = sheetNamed(book, PAYOUT);
  if (sheet.getLastRow() > 0) return;
  const T = "'" + TOTALS + "'";
  sheet.getRange("B1").setValue("Выплата зарплаты").setFontWeight("bold").setFontSize(14);
  sheet.getRange("B2").setFormula(F('="Период: "&TEXT(' + T + '!B3,"dd.mm.yyyy")&" – "&TEXT(' + T + '!B4,"dd.mm.yyyy")'))
    .setFontWeight("bold");
  sheet.getRange("E2").setValue("Месяц и период меняются на листе «Итоги».").setFontColor("#7a6f66");
  sheet.getRange("B3").setValue("Итого к выплате:").setFontWeight("bold");
  sheet.getRange("C3").setFormula(F('=SUMIF(F6:F,">0")')).setNumberFormat(MONEY + ' "₸"').setFontWeight("bold");
  sheet.getRange("D3").setFormula(F('=COUNTIF(I6:I,"?*")&" — проверить"')).setFontColor("#b42318");

  const q = "select Z, F, G, sum(AD), sum(T), sum(AE) " +
    "where A is not null and B >= date '\"&TEXT(" + T + "!$B$3,\"yyyy-mm-dd\")&\"' and B <= date '\"&TEXT(" + T + "!$B$4,\"yyyy-mm-dd\")&\"' " +
    "group by Z, F, G order by G, F " +
    "label Z 'Код', F 'Сотрудник', G 'Родной филиал', sum(AD) 'Начислено, ₸', sum(T) 'Авансы, ₸', sum(AE) 'К выплате, ₸'";
  sheet.getRange("A5").setFormula(F(
    "=IFERROR(QUERY('" + DATA + "'!A:AH,\"" + q + "\",1),\"\")"
  ));
  sheet.getRange("B4").setFormula(F('=IF(COUNTA(A6:A)=0,"За этот период отметок пока нет","")')).setFontColor("#7a6f66");
  const look = (col) => "IFERROR(VLOOKUP(A6:A,'" + STAFF + "'!A:J," + col + ",0),\"\")";
  sheet.getRange("G5").setFormula(F('={"Kaspi Gold";ARRAYFORMULA(IF(A6:A="",,' + look(9) + '))}'));
  sheet.getRange("H5").setFormula(F('={"Имя в Kaspi";ARRAYFORMULA(IF(A6:A="",,' + look(10) + '))}'));
  sheet.getRange("I5").setFormula(F('={"Проверить";ARRAYFORMULA(IF(A6:A="",,' +
    'IF(G6:G="","нет Kaspi Gold",IF(F6:F<0,"к выплате минус",""))))}'));

  styleHeader(sheet.getRange("A5:I5"));
  sheet.setRowHeight(5, 36);
  sheet.setFrozenRows(5);
  sheet.hideColumns(1);
  sheet.getRange("D6:F").setNumberFormat(MONEY);
  sheet.getRange("F6:F").setFontWeight("bold");
  sheet.getRange("I6:I").setFontColor("#b42318");
  sheet.setColumnWidth(2, 180);
  sheet.setColumnWidth(3, 120);
  sheet.setColumnWidths(4, 3, 110);
  sheet.setColumnWidth(7, 150);
  sheet.setColumnWidth(8, 150);
  sheet.setColumnWidth(9, 150);
  sheet.protect().setDescription("Считается само из «Журнала» и «Сотрудников»").setWarningOnly(true);
}

/** Перестроить только лист «Выплата» (он считается формулами — данные не теряются). */
function rebuildPayout() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const old = book.getSheetByName(PAYOUT);
  if (old) book.deleteSheet(old);
  setup();
}

function buildHelp(book) {
  const sheet = sheetNamed(book, HELP);
  sheet.clear(); // памятка короткая — каждый раз пишем заново под текущую версию
  const lines = [
    ["Зарплатная таблица «ОТ Донер»"],
    [""],
    ["«Итоги» — сколько платить каждому за период. Вверху выберите месяц и период: 1–15, 16–конец месяца или весь месяц."],
    ["   По каждому человеку: смены (включая замены на других филиалах), опоздания, невыходы, переработка и недоработка,"],
    ["   ставки, деньги за часы, бонусы, штрафы, авансы и итог к выплате."],
    ["«Выплата» — готовый список для бухгалтера за тот же период: к выплате, Kaspi Gold и имя получателя."],
    ["   Колонка «Проверить» подсказывает, у кого нет Kaspi Gold или сумма к выплате ушла в минус."],
    ["   Бухгалтеру можно дать доступ к таблице или скачать лист: Файл → Скачать → PDF или Excel."],
    ["«Ставки» — сколько платим за смену по должности в каждом филиале. Заполните жёлтые клетки один раз:"],
    ["   каждая отмеченная смена посчитается сама. Новые сочетания филиал + должность появятся здесь сами."],
    ["«Сотрудники» — список от приложения: родной филиал, должность, Kaspi Gold и имя в Kaspi."],
    ["   «Своя ставка» — только для тех, кому платите не как всем на их должности. Она важнее ставки по должности"],
    ["   и действует на всех его сменах. Пустая клетка (например, ночь) — берётся ставка должности."],
    ["«Журнал» — каждую строку заполняет приложение. Руками здесь ничего менять не нужно:"],
    ["   изменения делаются в приложении (шеф — в панели «Зарплата»)."],
    ["   Строка «Смена» — кто вышел, на какую смену, опоздание, часы, штрафы, бонусы, замена. «Аванс» — выданные деньги."],
    ["   Цвета: красный — не вышел, жёлтый — опоздал, зелёный — замена, голубой — аванс."],
    [""],
    ["Как считается:"],
    ["   Ставка за смену — по филиалу, где человек работал, и должности в эту смену. Полная пустая = день + ночь."],
    ["   Цена часа = ставка ÷ часы смены. Например, 8 000 ₸ за смену 10:00–18:00 (8 часов) → 1 000 ₸ в час."],
    ["   За часы = (переработка − недоработка) × цена часа."],
    ["   Начислено = ставка + за часы − штрафы + бонусы. К выплате = начислено − авансы."],
    ["   Замена с другого филиала или из резерва получает ставку того филиала, где работала. Не вышел — ставка 0."],
    ["   Штрафы за опоздание считает приложение по ступеням из панели «Зарплата → Правила штрафов»."],
  ];
  sheet.getRange(1, 1, lines.length, 1).setValues(lines);
  sheet.getRange("A1").setFontWeight("bold").setFontSize(14);
  sheet.setColumnWidth(1, 900);
}
