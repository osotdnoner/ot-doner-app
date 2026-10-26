/** @OnlyCurrentDoc */
/**
 * ОТ Донер — зарплатная таблица.
 *
 * Этот скрипт живёт внутри Google Таблицы. Он:
 *  1) строит листы «Итоги», «Ставки», «Журнал», «Как пользоваться» с формулами (функция setup);
 *  2) принимает данные из приложения (функция doPost): строки — на лист «Журнал»,
 *     список сотрудников — на лист «Ставки».
 *
 * Строки «Журнала» двух видов:
 *  • «Смена» — один человек в одной смене: филиал, смена (день, ночь или полная), выход, опоздание,
 *    переработка и недоработка в часах, штрафы, бонусы, замена;
 *  • «Аванс» — деньги, выданные заранее.
 * Если админ поправил отметку или шеф снял штраф, строка обновляется. «Выходной», удалённая замена
 * или удалённый аванс — строка удаляется.
 *
 * «Ставки» — у каждого сотрудника своя ставка за дневную, ночную и полную смену. Шеф вписывает их
 * в жёлтые клетки. Цену часа, деньги за часы, начислено, к выплате и итоги считают формулы.
 *
 * Если скрипт обновили (новая раскладка), выполните rebuild: листы перестроятся, а приложение
 * отправит все данные заново. Заполненные личные ставки сохраняются.
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
const RATES = "Ставки";
const OLD_RATES = "Ставки по должностям (старые)";
const TOTALS = "Итоги";
const HELP = "Как пользоваться";

// Колонки, которые присылает приложение (A–Z). Порядок совпадает с payroll/sheets.py.
const HEADERS = [
  "Ключ", "Дата", "Филиал", "Что", "Смена", "Сотрудник", "Родной филиал", "Должность", "Выход", "Пришёл в",
  "Опоздание, мин", "Причина", "Переработка, ч", "Недоработка, ч", "Часы: почему", "Штраф, ₸", "За что штраф",
  "Бонус, ₸", "За что бонус", "Аванс, ₸", "Замена", "Комментарий", "Кто отметил", "Обновлено", "Часов в смене",
  "Код сотрудника",
];
// Что считают формулы (AA–AH): ставка, цена часа, за часы, начислено, к выплате, смен, опозданий, невыходов

// Лист «Ставки»: A код (скрыт) · B сотрудник · C родной филиал · D должность · E день · F ночь · G полная · H статус
const RATE_HEADERS = ["Код", "Сотрудник", "Родной филиал", "Должность", "День, ₸", "Ночь, ₸", "Полная смена, ₸", "Статус"];

// Филиалы — для итогов по филиалам
const BRANCHES = ["27 мкрн", "16 мкрн", "17 мкрн", "14 мкрн"];

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

    // 0. Список сотрудников → «Ставки» (имя, филиал, должность; ставки не трогаем)
    const staffAdded = data.staff ? updateStaff(book, data.staff, true) : 0;

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

    // 3. Если человека ещё нет в «Ставках» (список не дошёл) — добавить по строке журнала
    const fromRows = (data.rows || []).map((row) => [row[25], row[5], row[6], row[7], ""]).filter((s) => s[0]);
    updateStaff(book, fromRows, false);
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
 * Список сотрудников на листе «Ставки». staff — строки [код, имя, родной филиал, должность, статус].
 * overwrite: обновлять ли имя, филиал, должность и статус у тех, кто уже есть. Ставки (E–G) не трогаются никогда.
 * Возвращает, сколько людей добавлено.
 */
function updateStaff(book, staff, overwrite) {
  const sheet = book.getSheetByName(RATES);
  if (!sheet || !staff.length) return 0;
  const last = sheet.getLastRow();
  const n = Math.max(last - 1, 0);
  const codes = n ? sheet.getRange(2, 1, n, 1).getValues().map((r) => String(r[0])) : [];
  const info = n ? sheet.getRange(2, 2, n, 3).getValues() : [];
  const status = n ? sheet.getRange(2, 8, n, 1).getValues() : [];
  const fresh = [];
  let changed = false;
  staff.forEach((s) => {
    const code = String(s[0]);
    const i = codes.indexOf(code);
    if (i < 0) {
      codes.push(code);
      fresh.push([code, s[1], s[2], s[3], "", "", "", s[4] || "работает"]);
    } else if (overwrite) {
      info[i] = [s[1], s[2], s[3]];
      status[i] = [s[4] || ""];
      changed = true;
    }
  });
  if (changed) {
    sheet.getRange(2, 2, n, 3).setValues(info);
    sheet.getRange(2, 8, n, 1).setValues(status);
  }
  if (fresh.length) {
    sheet.getRange(last + 1, 1, fresh.length, RATE_HEADERS.length).setValues(fresh);
    // По филиалу, потом по имени: новый сотрудник встаёт к своим
    sheet.getRange(2, 1, n + fresh.length, RATE_HEADERS.length).sort([{ column: 3, ascending: true }, { column: 2, ascending: true }]);
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
 * все отметки, авансы и список сотрудников ещё раз (команда sync_sheet). Личные ставки сохраняются.
 * Старый лист ставок по должностям, если в нём что-то вписано, остаётся рядом под другим именем.
 */
function rebuild() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.insertSheet("временный"); // в таблице всегда должен оставаться хотя бы один лист
  [TOTALS, DATA, HELP].concat(OLD_DATA).forEach((name) => {
    const s = book.getSheetByName(name);
    if (s) book.deleteSheet(s);
  });
  const rates = book.getSheetByName(RATES);
  if (rates && rates.getRange("B1").getValue() !== RATE_HEADERS[1]) { // старая раскладка: по должностям
    if (hasRates(rates)) {
      rates.setName(book.getSheetByName(OLD_RATES) ? OLD_RATES + " " + Date.now() : OLD_RATES);
    } else {
      book.deleteSheet(rates);
    }
  }
  setup(); // setup сам уберёт временный лист
}

function hasRates(sheet) {
  const last = sheet.getLastRow();
  if (last < 2) return false;
  return sheet.getRange(2, 4, last - 1, 2).getValues().some((r) => r.some((v) => typeof v === "number" && v > 0));
}

// ---------------------------------------------------------------- построение таблицы

function setup() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.setSpreadsheetTimeZone("Asia/Almaty");
  buildRates(book);
  buildData(book);
  buildTotals(book);
  buildHelp(book);
  ["Лист1", "Sheet1", "Лист 1", "временный"].forEach((name) => {
    const s = book.getSheetByName(name);
    if (s && s.getLastRow() === 0 && book.getSheets().length > 1) book.deleteSheet(s);
  });
  [TOTALS, RATES, DATA, HELP].forEach((name, i) => {
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

function buildData(book) {
  const sheet = sheetNamed(book, DATA);
  if (sheet.getLastRow() > 0) return; // уже построен — данные не трогаем
  sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  const arr = (title, body) => '={"' + title + '";ARRAYFORMULA(IF(A2:A="",,' + body + '))}';
  // Ставка — личная, по коду сотрудника (Z) и виду смены (E). Авансам и невыходам — 0.
  // Полная: своя сумма из «Ставок», а если она пустая — день + ночь.
  const look = (col) => "VLOOKUP(Z2:Z,'" + RATES + "'!A:G," + col + ",0)";
  sheet.getRange("AA1").setFormula(F(arr("Ставка, ₸",
    'IF((D2:D<>"Смена")+(I2:I="Не вышел"),0,IFERROR(IF(E2:E="День",' + look(5) + ',IF(E2:E="Ночь",' + look(6) +
    ',IF(' + look(7) + '="",' + look(5) + '+' + look(6) + ',' + look(7) + ')))*1,0))'
  )));
  // Цена часа = ставка ÷ часы смены; за часы = (переработка − недоработка) × цена часа
  sheet.getRange("AB1").setFormula(F(arr("Цена часа, ₸", "IF(Y2:Y>0,AA2:AA/Y2:Y,0)")));
  sheet.getRange("AC1").setFormula(F(arr("За часы, ₸", "ROUND((M2:M-N2:N)*AB2:AB)")));
  sheet.getRange("AD1").setFormula(F(arr("Начислено, ₸", "AA2:AA+AC2:AC-P2:P+R2:R")));
  sheet.getRange("AE1").setFormula(F(arr("К выплате, ₸", "AD2:AD-T2:T")));
  sheet.getRange("AF1").setFormula(F(arr("Смен", 'IF((D2:D="Смена")*(I2:I<>"Не вышел"),1,0)')));
  sheet.getRange("AG1").setFormula(F(arr("Опозданий", 'IF(I2:I="Опоздал",1,0)')));
  sheet.getRange("AH1").setFormula(F(arr("Невыходов", 'IF(I2:I="Не вышел",1,0)')));

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
  if (sheet.getLastRow() > 0) return; // уже есть — ставки шефа не трогаем
  sheet.getRange(1, 1, 1, RATE_HEADERS.length).setValues([RATE_HEADERS]);
  styleHeader(sheet.getRange(1, 1, 1, RATE_HEADERS.length));
  sheet.setFrozenRows(1);
  sheet.hideColumns(1);
  sheet.getRange("E2:G").setNumberFormat(MONEY).setBackground(INPUT_BG);
  sheet.setColumnWidth(2, 170);
  sheet.setColumnWidth(3, 110);
  sheet.setColumnWidth(4, 160);
  sheet.setColumnWidths(5, 3, 115);
  sheet.setColumnWidth(8, 90);
  sheet.getRange("B1").setNote("Список ведёт приложение: новый сотрудник появляется здесь сам. " +
    "Имя, филиал и должность меняйте в приложении (панель «Сотрудники»).");
  sheet.getRange("E1").setNote("Сколько платим этому человеку за одну дневную смену.");
  sheet.getRange("F1").setNote("Сколько платим за одну ночную смену.");
  sheet.getRange("G1").setNote("Полная смена (с начала дневной до конца ночной). Пусто — считается день + ночь. " +
    "Если платите иначе, впишите свою сумму.");
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

function buildHelp(book) {
  const sheet = sheetNamed(book, HELP);
  if (sheet.getLastRow() > 0) return;
  const lines = [
    ["Зарплатная таблица «ОТ Донер»"],
    [""],
    ["«Итоги» — сколько платить каждому за период. Вверху выберите месяц и период: 1–15, 16–конец месяца или весь месяц."],
    ["   По каждому человеку: смены (включая замены на других филиалах), опоздания, невыходы, переработка и недоработка,"],
    ["   ставки, деньги за часы, бонусы, штрафы, авансы и итог к выплате."],
    ["«Ставки» — у каждого сотрудника своя ставка за дневную, ночную и полную смену. Заполните жёлтые клетки."],
    ["   Список сотрудников ведёт приложение: новый человек появляется здесь сам. Полная смена пустая = день + ночь."],
    ["«Журнал» — каждую строку заполняет приложение. Руками здесь ничего менять не нужно:"],
    ["   изменения делаются в приложении (шеф — в панели «Зарплата»)."],
    ["   Строка «Смена» — кто вышел, на какую смену, опоздание, часы, штрафы, бонусы, замена. «Аванс» — выданные деньги."],
    ["   Цвета: красный — не вышел, жёлтый — опоздал, зелёный — замена, голубой — аванс."],
    [""],
    ["Как считается:"],
    ["   Цена часа = ставка ÷ часы смены. Например, 8 000 ₸ за смену 10:00–18:00 (8 часов) → 1 000 ₸ в час."],
    ["   За часы = (переработка − недоработка) × цена часа."],
    ["   Начислено = ставка + за часы − штрафы + бонусы. К выплате = начислено − авансы."],
    ["   Замена получает свою личную ставку. Если сотрудник не вышел, ставка 0."],
    ["   Штрафы за опоздание считает приложение по ступеням из панели «Зарплата → Правила штрафов»."],
  ];
  sheet.getRange(1, 1, lines.length, 1).setValues(lines);
  sheet.getRange("A1").setFontWeight("bold").setFontSize(14);
  sheet.setColumnWidth(1, 900);
}
